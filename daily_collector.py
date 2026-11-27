#!/usr/bin/env python3
"""
Daily 1-min OHLCV collector for active near-term F&O contracts.

Scope: NIFTY (weekly), SENSEX (weekly), CRUDEOIL (monthly) options --
nearest unexpired expiry only per underlying (same query as
select_active_contracts.sql). No liquidity/OI filtering yet: thresholds
are unset, so every strike at the near-term expiry is collected. That
filtering gets added once real volume/OI data is visible.

For each active contract, for the given trade_date:
  1. Fetch 1-min candles from Fyers (fyers.history).
  2. Upsert them into ohlcv_1min.
  3. Call refresh_collection_log(contract_id, trade_date) to record
     OK/PARTIAL/MISSING against trading_calendar's expected_bars.

Usage:
  python3 daily_collector.py --date 2026-09-02
  python3 daily_collector.py                     # defaults to yesterday (IST)
"""
import argparse
import datetime
import subprocess
import sys
import time
import traceback

from fyers_client import fyers
from telegram_alerts import alert_crash, alert_summary, alert_anomaly, alert_backfill, ANOMALY_DROP_THRESHOLD

DB = "fno_archive"
PSQL = ["sudo", "-u", "postgres", "psql", "-d", DB]

# Fyers historical API rate limits are per-second/per-minute; this is a
# conservative fixed delay between sequential contract fetches rather than
# a burst-then-block strategy, since ~1,174 contracts/day is well within
# budget at this pace (~1,174 * 0.35s ~= 7 min/run).
REQUEST_DELAY_SECONDS = 0.35
MAX_RETRIES = 3

# Fyers error codes that mean "the token/session itself is bad", not "this
# one contract had a problem". These are systemic -- every remaining
# contract in the run will fail the same way, so retrying per-contract
# just burns ~7 minutes confirming what the first failure already showed.
AUTH_ERROR_CODES = {-16, -17, -8}

VALID_UNDERLYINGS = {"NIFTY", "SENSEX", "CRUDEOIL"}

# Hoisted from main() (was a local) -- now shared with the backfill path
# too, since a backfill attempt with a high error rate is real signal
# (not the staleness that gap-detection already filters out separately)
# and deserves the same threshold.
ERROR_RATE_FAIL_THRESHOLD = 0.25

# Single cap for both underlying classes, not tuned per NIFTY/SENSEX
# (weekly) vs CRUDEOIL (monthly): the REAL bound on recoverability is
# the per-gap-day expiry check in find_backfillable_gaps(), which
# correctly rejects anything actually expired regardless of how far
# back this cap lets the query look. This is a defensive backstop only
# (e.g. collection_log somehow empty) -- on any normal day, last_good_day
# in the gap query already limits the scan to the real gap size.
BACKFILL_HARD_CAP_DAYS = 30


class FyersAuthError(RuntimeError):
    """Raised when Fyers rejects the request due to an expired/invalid
    token, as opposed to a per-contract data problem."""


def run_psql(sql, fetch=False):
    """Runs `sql` via psql. With fetch=True, returns stdout in
    unaligned, pipe-delimited, no-header form for parsing."""
    cmd = list(PSQL)
    if fetch:
        cmd += ["-t", "-A", "-F", "|"]
    cmd += ["-c", sql]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr}")
    return result.stdout


def get_active_contracts(trade_date, underlyings):
    """Nearest unexpired expiry per underlying, all strikes/CE+PE.
    Mirrors select_active_contracts.sql, parameterized on trade_date so a
    backfill run for a past date still resolves 'nearest expiry as of
    that date' correctly rather than always using today's date.
    `underlyings` scopes which symbols this run covers -- e.g. the split
    NSE (NIFTY, SENSEX, 16:00 IST) vs MCX (CRUDEOIL, 23:56 IST) cron
    entries each pass only their own subset."""
    symbol_list = ", ".join(f"'{u}'" for u in underlyings)
    sql = f"""
    WITH near_term AS (
        SELECT c.instrument_id, MIN(c.expiry_date) AS nearest_expiry
        FROM contracts c
        JOIN instruments i ON i.instrument_id = c.instrument_id
        WHERE i.symbol IN ({symbol_list})
          AND c.expiry_date >= '{trade_date.isoformat()}'
        GROUP BY c.instrument_id
    )
    SELECT c.contract_id, c.trading_symbol, i.exchange, i.symbol
    FROM contracts c
    JOIN instruments i ON i.instrument_id = c.instrument_id
    JOIN near_term nt ON nt.instrument_id = c.instrument_id
                      AND nt.nearest_expiry = c.expiry_date
    ORDER BY i.symbol, c.strike_price, c.option_type;
    """
    out = run_psql(sql, fetch=True)
    contracts = []
    for line in out.strip().splitlines():
        if not line:
            continue
        contract_id, trading_symbol, exchange, underlying = line.split("|")
        contracts.append({
            "contract_id": int(contract_id),
            "trading_symbol": trading_symbol,
            "exchange": exchange,
            "underlying": underlying,
        })
    return contracts


def get_trailing_with_data_avg(trade_date, underlyings, lookback_days=7):
    """Per-underlying average count of contracts with actual_bars > 0 over
    the most recent `lookback_days` distinct trade_dates strictly before
    `trade_date` that have any collection_log rows at all. Returns {} if
    there's no history yet (e.g. day one) -- anomaly detection needs
    something to compare against and silently skips rather than guesses.

    `underlyings` filters per_day to only the symbols actually being
    compared -- required, not optional, once the collector runs as
    separate per-scope processes (NSE at 16:00, MCX at 23:56): without
    this filter, a NIFTY+SENSEX-only run's trailing average would still
    include CRUDEOIL's historical average (pulled in from the OTHER
    process's past runs), silently inflating the denominator and
    guaranteeing a false-positive anomaly alert on every partial run."""
    symbol_list = ", ".join(f"'{u}'" for u in underlyings)
    sql = f"""
    WITH recent_dates AS (
        SELECT DISTINCT trade_date
        FROM collection_log
        WHERE trade_date < '{trade_date.isoformat()}'
        ORDER BY trade_date DESC
        LIMIT {lookback_days}
    ),
    per_day AS (
        SELECT i.symbol AS underlying, cl.trade_date,
               count(*) FILTER (WHERE cl.actual_bars > 0) AS with_data
        FROM collection_log cl
        JOIN contracts c ON c.contract_id = cl.contract_id
        JOIN instruments i ON i.instrument_id = c.instrument_id
        WHERE cl.trade_date IN (SELECT trade_date FROM recent_dates)
          AND i.symbol IN ({symbol_list})
        GROUP BY i.symbol, cl.trade_date
    )
    SELECT underlying, avg(with_data)
    FROM per_day
    GROUP BY underlying;
    """
    out = run_psql(sql, fetch=True)
    averages = {}
    for line in out.strip().splitlines():
        if not line:
            continue
        underlying, avg_str = line.split("|")
        averages[underlying] = float(avg_str)
    return averages


def fetch_candles(fy_symbol, trade_date):
    """Fetches 1-min candles for one contract on one date. Returns a list
    of [epoch_ts, open, high, low, close, volume] rows, or [] if the
    contract had no trades that day (e.g. thin/no volume at this strike --
    expected and not an error, since liquidity filtering isn't applied
    yet)."""
    data = {
        "symbol": fy_symbol,
        "resolution": "1",
        "date_format": "1",   # range_from/range_to as 'YYYY-MM-DD' strings
        "range_from": trade_date.isoformat(),
        "range_to": trade_date.isoformat(),
        "cont_flag": "0",
    }

    for attempt in range(MAX_RETRIES):
        response = fyers.history(data)
        status = response.get("s")

        if status == "ok":
            return response.get("candles", [])
        if status == "no_data":
            return []
        if response.get("code") == 429:
            time.sleep(5 * (attempt + 1))
            continue
        if response.get("code") in AUTH_ERROR_CODES:
            raise FyersAuthError(f"Fyers auth error for {fy_symbol}: {response}")
        # Any other error (bad symbol, no such contract, etc.) -- don't
        # retry, surface it so the caller can log and move on to the next
        # contract; this kind of error is specific to one symbol.
        raise RuntimeError(f"Fyers error for {fy_symbol}: {response}")

    raise RuntimeError(f"Fyers rate-limit retries exhausted for {fy_symbol}")


def build_upsert_sql(contract_id, candles):
    if not candles:
        return None

    # Fyers has been observed returning 2+ candles for the same epoch_ts
    # within one response (confirmed live 2026-09-04 -- crashed the whole
    # run via Postgres's own restriction: ON CONFLICT DO UPDATE cannot
    # affect the same row twice within one statement). Dedupe by epoch_ts
    # before building the INSERT -- keeps the LAST occurrence per
    # timestamp (assumption: a later duplicate in Fyers' own response
    # order is more likely a revision/correction than the earlier one --
    # NOT confirmed against Fyers docs, so logged when it actually fires
    # rather than silently resolved forever on an unverified guess).
    deduped = {}
    for c in candles:
        deduped[c[0]] = c   # c[0] is epoch_ts; last write wins
    if len(deduped) < len(candles):
        print(f"  build_upsert_sql: deduped {len(candles) - len(deduped)} "
              f"duplicate-timestamp candle(s) for contract_id={contract_id}")

    rows = []
    for epoch_ts, c in deduped.items():
        _, o, h, l, cl, v = c[:6]
        rows.append(
            f"(to_timestamp({epoch_ts}), {contract_id}, {o}, {h}, {l}, {cl}, {int(v)}, NULL)"
        )
    values = ",\n".join(rows)
    return f"""
    INSERT INTO ohlcv_1min (time, contract_id, open, high, low, close, volume, oi)
    VALUES
    {values}
    ON CONFLICT (contract_id, time) DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume;
    """


def refresh_log(contract_id, trade_date):
    run_psql(f"SELECT refresh_collection_log({contract_id}, '{trade_date.isoformat()}'::date);")


def find_gap_days(underlying, trade_date, hard_cap_days=BACKFILL_HARD_CAP_DAYS):
    """Trading days (per trading_calendar) strictly before trade_date,
    after the last day this underlying had ANY real coverage (status
    OK/PARTIAL), that have ZERO OK/PARTIAL rows. Deliberately NOT "any
    contract shows MISSING" -- with no liquidity filtering yet, ~57% of
    individual contracts legitimately show MISSING every single day
    (thin/untraded strikes), so that definition would flag every day as
    a gap. This instead asks "did the collector get ANY real coverage
    for this underlying that day at all" -- the same whole-day signal
    alert_anomaly already uses, just applied backwards to find missed
    days instead of forward to compare against a trailing average.

    last_good_day self-bounds the scan to the real gap size on any
    normal day (no gap -> zero extra rows scanned); hard_cap_days is
    only a defensive backstop for a pathological case (e.g.
    collection_log itself somehow empty)."""
    sql = f"""
    WITH last_good_day AS (
        SELECT MAX(cl.trade_date) AS d
        FROM collection_log cl
        JOIN contracts c ON c.contract_id = cl.contract_id
        JOIN instruments i ON i.instrument_id = c.instrument_id
        WHERE i.symbol = '{underlying}'
          AND cl.status IN ('OK', 'PARTIAL')
    ),
    candidate_days AS (
        SELECT tc.trade_date
        FROM trading_calendar tc
        JOIN instruments i ON i.exchange = tc.exchange
        WHERE i.symbol = '{underlying}'
          AND tc.is_trading_day = true
          AND tc.trade_date < '{trade_date.isoformat()}'
          AND tc.trade_date > COALESCE(
                (SELECT d FROM last_good_day),
                '{trade_date.isoformat()}'::date - INTERVAL '{hard_cap_days} days'
              )
    ),
    covered_days AS (
        SELECT DISTINCT cl.trade_date
        FROM collection_log cl
        JOIN contracts c ON c.contract_id = cl.contract_id
        JOIN instruments i ON i.instrument_id = c.instrument_id
        WHERE i.symbol = '{underlying}'
          AND cl.status IN ('OK', 'PARTIAL')
          AND cl.trade_date IN (SELECT trade_date FROM candidate_days)
    )
    SELECT cd.trade_date
    FROM candidate_days cd
    LEFT JOIN covered_days ON covered_days.trade_date = cd.trade_date
    WHERE covered_days.trade_date IS NULL
    ORDER BY cd.trade_date;
    """
    out = run_psql(sql, fetch=True)
    return [datetime.date.fromisoformat(line) for line in out.strip().splitlines() if line]


def get_nearest_expiry_for_date(underlying, as_of_date):
    """The near-term expiry_date that was active for `underlying` as of
    as_of_date -- used ONLY to check backfill recoverability. Every
    contract in that near-term set shares this exact expiry_date (it's
    grouped by MIN(expiry_date) per instrument), so one comparison
    against TODAY (not as_of_date) tells us whether ALL of that day's
    contracts are still fetchable: Fyers permanently refuses history
    for contracts that have since expired, regardless of how valid they
    were on as_of_date itself (confirmed empirically 2026-09-04/05:
    {'code': -300, 'message': 'Invalid symbol provided'} for every
    contract of an underlying whose near-term expiry had passed).
    Returns None if no contract set is found at all for that date."""
    sql = f"""
    SELECT MIN(c.expiry_date)
    FROM contracts c
    JOIN instruments i ON i.instrument_id = c.instrument_id
    WHERE i.symbol = '{underlying}'
      AND c.expiry_date >= '{as_of_date.isoformat()}';
    """
    out = run_psql(sql, fetch=True).strip()
    return datetime.date.fromisoformat(out) if out else None


def run_backfill_day(underlying, gap_date):
    """Attempts one recoverable gap day for one underlying -- same
    fetch/upsert/refresh_log mechanics as main()'s own loop, just scoped
    to a single historical date. Always sends alert_backfill() on
    completion (success or high error rate -- the numbers speak for
    themselves, no separate failure-alert type needed). A systemic auth
    error alerts via the existing alert_crash() (same urgency class as a
    normal run's auth failure) and returns immediately -- today's real
    collection will independently hit and report the same dead token via
    its own existing fail-fast logic, so no special handling is needed
    here to "stop everything"; this function just stops trying further
    backfill for THIS underlying."""
    contracts = get_active_contracts(gap_date, [underlying])
    ok = missing = errors = 0
    bars_written = 0

    for c in contracts:
        fy_symbol = f"{c['exchange']}:{c['trading_symbol']}"
        try:
            candles = fetch_candles(fy_symbol, gap_date)
        except FyersAuthError as e:
            print(f"  backfill AUTH ERROR {underlying} {gap_date} {fy_symbol}: {e}",
                  file=sys.stderr)
            alert_crash(gap_date, 1,
                        f"Backfill auth error for {underlying} {gap_date}: {e}",
                        log_path_hint(gap_date, [underlying]))
            return
        except RuntimeError as e:
            print(f"  backfill ERROR {underlying} {gap_date} {fy_symbol}: {e}",
                  file=sys.stderr)
            errors += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        if candles:
            ok += 1
            bars_written += len(candles)
        else:
            missing += 1

        sql = build_upsert_sql(c["contract_id"], candles)
        if sql:
            run_psql(sql)
        refresh_log(c["contract_id"], gap_date)
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  backfill done: {underlying} {gap_date} -- {ok} ok, {missing} missing, "
          f"{errors} errors, {bars_written} bars")
    alert_backfill(gap_date, underlying, ok, missing, errors, bars_written)


def run_backfill(underlyings, trade_date):
    """Runs BEFORE today's normal collection (see main()). For each
    underlying in scope: finds gap days since the last known-good day,
    and for each one, checks recoverability BEFORE attempting any fetch
    -- an already-expired gap is skipped with a log line only, no
    Telegram alert (per design: that's permanently unrecoverable noise,
    not something worth paging on every run). Never raises/exits --
    today's real collection below must proceed regardless of what
    happens here."""
    today = datetime.date.today()
    for underlying in underlyings:
        gaps = find_gap_days(underlying, trade_date)
        for gap_date in gaps:
            nearest_expiry = get_nearest_expiry_for_date(underlying, gap_date)
            if nearest_expiry is None or nearest_expiry < today:
                print(f"  backfill skip: {underlying} {gap_date} -- contract "
                      f"already expired (expiry {nearest_expiry}), unrecoverable")
                continue
            print(f"  backfill attempt: {underlying} {gap_date} "
                  f"(expiry {nearest_expiry}, still active)")
            run_backfill_day(underlying, gap_date)


# Suffix derived from the underlyings selection itself, not a separate
# flag that could drift out of sync with --underlyings: matches
# run_daily_collector_nse.sh/_mcx.sh's own log filenames exactly, so a
# crash alert's Log: line always points at the right file. Any other
# combination (the default all-three case, or manual/backfill use)
# keeps the original unsuffixed name.
def _log_suffix(underlyings):
    scope = set(underlyings)
    if scope == {"NIFTY", "SENSEX"}:
        return "_nse"
    if scope == {"CRUDEOIL"}:
        return "_mcx"
    return ""


def log_path_hint(trade_date, underlyings):
    suffix = _log_suffix(underlyings)
    return (f"logs/collector_{trade_date.isoformat()}{suffix}.log "
            f"(on the box, /opt/fno-archive/logs/)")


def fail(trade_date, underlyings, message):
    """Single choke point for a FAILED run: prints FATAL, alerts Telegram,
    exits 1. Used by every failure path so none of them can silently
    exit 0 -- the original gap this whole error-handling pass closed."""
    print(f"FATAL: {message}", file=sys.stderr)
    log_path = log_path_hint(trade_date, underlyings)
    alert_crash(trade_date, 1, message, log_path)
    sys.exit(1)


def main(trade_date, underlyings, dry_run):
    """trade_date/underlyings/dry_run are passed in, not parsed here --
    computed exactly once in __main__, before this function (or anything
    that could run long enough to cross midnight) starts. This is the
    fix for a real bug: the top-level exception handler used to fall
    back to datetime.date.today() when a crash bypassed fail()'s own
    alert path, which silently returned the WRONG date for any run that
    started before midnight and crashed after it (confirmed live
    2026-09-04/05 -- the crash alert read "Date: 2026-09-05" for a run
    that was actually processing trade_date=2026-09-04, since today()
    was evaluated at catch-time, after the date had already rolled
    over). Now there is exactly one computation site for trade_date in
    the whole file -- every consumer (this loop, fail(), and __main__'s
    except block) reads the same value, so this class of drift can't
    recur."""
    if not dry_run:
        # Runs before today's own collection, scoped to the same
        # underlyings this invocation already covers -- reuses the split
        # architecture directly (the NSE run only checks NIFTY/SENSEX
        # gaps, MCX's only checks CRUDEOIL's). Skipped entirely in
        # dry-run mode, matching dry-run's own "no DB writes" contract.
        print("Checking for backfillable gaps...")
        run_backfill(underlyings, trade_date)

    contracts = get_active_contracts(trade_date, underlyings)
    print(f"{len(contracts)} active near-term contracts for {trade_date}")

    ok = missing = errors = 0
    bars_written = 0
    by_underlying_with_data = {c["underlying"]: 0 for c in contracts}

    for i, c in enumerate(contracts, 1):
        fy_symbol = f"{c['exchange']}:{c['trading_symbol']}"
        try:
            candles = fetch_candles(fy_symbol, trade_date)
        except FyersAuthError as e:
            # Systemic, not per-contract: every remaining contract will
            # fail identically, so stop immediately rather than spending
            # ~7 minutes reconfirming a dead token 1,174 times.
            print(f"  [{i}/{len(contracts)}] AUTH ERROR {fy_symbol}: {e}", file=sys.stderr)
            fail(trade_date, underlyings, f"Fyers authentication failed after "
                 f"{i-1}/{len(contracts)} contracts. Token is likely "
                 f"expired or invalid -- run generate_token.py.\n{e}")
        except RuntimeError as e:
            print(f"  [{i}/{len(contracts)}] ERROR {fy_symbol}: {e}", file=sys.stderr)
            errors += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        if candles:
            ok += 1
            by_underlying_with_data[c["underlying"]] += 1
            bars_written += len(candles)
        else:
            missing += 1

        if dry_run:
            print(f"  [{i}/{len(contracts)}] {fy_symbol}: {len(candles)} candles (dry-run, no write)")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        sql = build_upsert_sql(c["contract_id"], candles)
        if sql:
            run_psql(sql)
        refresh_log(c["contract_id"], trade_date)

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Done. {ok} contracts with data, {missing} with none, {errors} fetch errors.")

    # Non-auth errors are per-contract and don't abort the loop, but a run
    # where a large share of contracts errored out is not a healthy run
    # either -- flag it as FAILED rather than let a quiet "N fetch errors"
    # line hide behind an exit_code=0 that monitoring reads as success.
    if contracts and errors / len(contracts) > ERROR_RATE_FAIL_THRESHOLD:
        fail(trade_date, underlyings, f"{errors}/{len(contracts)} contracts errored "
             f"({errors / len(contracts):.0%}) -- exceeds "
             f"{ERROR_RATE_FAIL_THRESHOLD:.0%} threshold.")

    if dry_run:
        return  # no DB state changed, no alerts about a run that didn't happen

    log_path = log_path_hint(trade_date, underlyings)
    alert_summary(trade_date, len(contracts), ok, missing, errors,
                  by_underlying_with_data, bars_written)

    trailing_avg = get_trailing_with_data_avg(trade_date, by_underlying_with_data.keys())
    if trailing_avg:
        overall_avg = sum(trailing_avg.values())
        overall_today = sum(by_underlying_with_data.values())
        tripped = (
            overall_avg > 0 and overall_today / overall_avg < ANOMALY_DROP_THRESHOLD
        ) or any(
            by_underlying_with_data.get(sym, 0) == 0 and avg > 0
            for sym, avg in trailing_avg.items()
        )
        if tripped:
            alert_anomaly(trade_date, overall_today, overall_avg,
                          by_underlying_with_data, trailing_avg, log_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD, defaults to yesterday (IST)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Fetch and print counts only, no DB writes")
    parser.add_argument("--underlyings", default="NIFTY,SENSEX,CRUDEOIL",
                         help="Comma-separated subset of NIFTY,SENSEX,CRUDEOIL "
                              "(default: all three -- used by manual/backfill runs; "
                              "the split cron entries each pass their own scope)")
    args = parser.parse_args()

    # Computed exactly once, here, before any long-running work starts --
    # see main()'s own docstring for why this matters: a run that crosses
    # midnight must not have its reported date silently drift depending
    # on which code path catches a failure.
    if args.date:
        trade_date = datetime.date.fromisoformat(args.date)
    else:
        trade_date = datetime.date.today() - datetime.timedelta(days=1)

    underlyings = [u.strip().upper() for u in args.underlyings.split(",") if u.strip()]
    invalid = set(underlyings) - VALID_UNDERLYINGS
    if invalid:
        print(f"FATAL: invalid --underlyings value(s): {sorted(invalid)}. "
              f"Valid: {sorted(VALID_UNDERLYINGS)}", file=sys.stderr)
        sys.exit(1)

    try:
        main(trade_date, underlyings, args.dry_run)
    except SystemExit:
        raise
    except Exception:
        # Anything not already handled above (DB connection lost, coding
        # error, etc.) -- this is the actual "collector crashed entirely"
        # case Telegram alerting was originally asked to catch. Without
        # this, an unhandled exception would print a traceback to stderr
        # (captured by the wrapper's log) but never reach Telegram. Uses
        # the SAME trade_date computed above -- not datetime.date.today()
        # -- so this alert reports the date the run was actually
        # processing, not whatever the wall clock happens to read at the
        # moment the exception is caught.
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        alert_crash(trade_date, 1, tb.strip().splitlines()[-1] if tb else "unknown error",
                    log_path_hint(trade_date, underlyings))
        sys.exit(1)
