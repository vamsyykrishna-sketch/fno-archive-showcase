#!/usr/bin/env python3
"""
Telegram alerting for the daily collector. Four alert types, per the
design agreed earlier:

  1. CRASH   - the collector process failed/crashed entirely
               (auth error, unhandled exception, high error rate).
  2. SUMMARY - fires on every successful run, so silence itself becomes
               a signal that cron/the box died, not just that today was
               a quiet trading day.
  3. ANOMALY - contracts-with-data dropped sharply vs a trailing average,
               or a whole underlying returned zero on a trading day.
               Deliberately NOT "any contract shows MISSING", since
               ~57% of contracts legitimately have no trades with no
               liquidity filtering yet (see select_active_contracts.sql) --
               alerting on every MISSING would be ~600+ pings/night and
               get ignored by day two. This instead targets the failure
               modes that actually matter: expired token, API outage,
               stale symbol master.
  4. EXPIRY REMINDER - fires 2 and 1 days before each underlying's
               nearest expiry (see expiry_reminder.py, a deliberately
               separate script/cron entry from the collector -- the
               reminder's whole purpose is to catch collector failures,
               so it can't share the collector's own failure modes).
  5. BACKFILL - fires once per recoverable gap day actually attempted
               (see daily_collector.py's run_backfill()), success or
               high error rate alike -- the numbers in the message speak
               for themselves, so no separate failure-alert type is
               needed. Deliberately does NOT fire for unrecoverable
               (already-expired) gaps -- those are permanent and
               alerting on them every run would just be noise.
"""
import sys

import requests

from fyers_settings import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

ANOMALY_DROP_THRESHOLD = 0.5   # alert if today's with-data count is <50% of trailing avg


def send_telegram(message):
    """Best-effort send -- a Telegram outage must never crash the
    collector run itself. Failures are printed to stderr (captured by
    the wrapper script's log) rather than raised."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured (TELEGRAM_TOKEN/TELEGRAM_CHAT_ID "
              "missing from .env) -- skipping alert.", file=sys.stderr)
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"Telegram send failed: {resp.status_code} {resp.text}", file=sys.stderr)
    except Exception as e:
        print(f"Telegram error: {e}", file=sys.stderr)


def alert_crash(trade_date, exit_code, last_error, log_path):
    message = (
        f"\U0001F534 fno-archive collector FAILED\n"
        f"Date: {trade_date}\n"
        f"Exit code: {exit_code}\n\n"
        f"Last error:\n{last_error}\n\n"
        f"Log: {log_path}"
    )
    send_telegram(message)


def alert_expiry_reminder(underlying, expiry_date, days_until):
    message = (
        f"⏰ EXPIRY REMINDER — {underlying} expires in {days_until} "
        f"day(s): {expiry_date} ({expiry_date.strftime('%A')})\n"
        f"Make sure the Fyers token is refreshed and today's collection "
        f"has run before then — same-day collection is safer than "
        f"relying on this as a backfill substitute."
    )
    send_telegram(message)


def alert_summary(trade_date, total, with_data, no_trade, errors, by_underlying, bars_written):
    breakdown = " · ".join(f"{sym} {count}" for sym, count in by_underlying.items())
    message = (
        f"✅ fno-archive collector OK\n"
        f"Date: {trade_date}\n"
        f"Contracts: {total} processed, {errors} fetch errors\n\n"
        f"With data:  {with_data}  ({breakdown})\n"
        f"No trades:  {no_trade}\n"
        f"Bars written: {bars_written:,}"
    )
    send_telegram(message)


def alert_backfill(trade_date, underlying, ok, missing, errors, bars_written):
    message = (
        f"🔄 BACKFILL — {underlying} {trade_date}\n"
        f"Recovered: {ok} ok, {missing} missing, {errors} errors\n"
        f"Bars written: {bars_written:,}"
    )
    send_telegram(message)


def alert_anomaly(trade_date, today_with_data, trailing_avg, by_underlying_today,
                   by_underlying_avg, log_path):
    pct_drop = (1 - today_with_data / trailing_avg) if trailing_avg else 1.0

    affected_lines = "\n".join(
        f"  {sym} {by_underlying_today.get(sym, 0)} "
        f"(7-day avg {avg:.0f})"
        for sym, avg in by_underlying_avg.items()
        if avg > 0 and by_underlying_today.get(sym, 0) / avg < ANOMALY_DROP_THRESHOLD
    ) or "  (see per-underlying breakdown in log)"

    message = (
        f"⚠️ fno-archive collector ANOMALY\n"
        f"Date: {trade_date}\n\n"
        f"Contracts with data dropped sharply:\n"
        f"  today:     {today_with_data}\n"
        f"  7-day avg: {trailing_avg:.0f}  ({pct_drop:+.0%})\n\n"
        f"Affected:\n{affected_lines}\n\n"
        f"Possible causes: expired Fyers token, API outage, symbol "
        f"master stale.\n"
        f"Log: {log_path}"
    )
    send_telegram(message)
