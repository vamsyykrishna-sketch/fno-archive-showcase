#!/usr/bin/env python3
"""
Populates trading_calendar for NSE (F&O) and MCX from 2023-09-01 through 2027-08-31.

Sources (all primary, fetched and cross-checked 2026-09-01):
  NSE 2023: https://archives.nseindia.com/content/circulars/CMTR54757.pdf
  NSE 2024: https://nsearchives.nseindia.com/content/circulars/CMTR59722.pdf
  NSE 2025: https://nsearchives.nseindia.com/content/circulars/FAOP65588.pdf (F&O segment circular)
  NSE 2026: https://www.nseindia.com/api/holiday-master?type=trading (live API, "FO" key)
  MCX 2023: https://www.steelcitynettrade.com/Circulars/MCX%20Trading%20Holidays%20for%20the%20Calendar%20Year%202023.pdf
            (mirrors official circular MCX/TRD/710/2022)
  MCX 2024: mirrors MCX/TRD/872/2023
  MCX 2025: mirrors MCX/TRD/814/2024
  MCX 2026: mirrors MCX/TRD/636/2025

NSE holidays are always full-day closures (no partial-session concept in the
source circulars). MCX holidays fall into three patterns, taken verbatim from
each year's circular:
  - full closure (both sessions closed)
  - morning closed / evening open (the common festival pattern) -> 17:00-23:30
  - morning open / evening closed (New Year Day, every year 2023-2026) -> 09:00-17:00

2027 is NOT YET PUBLISHED by either exchange as of this population run
(2026-09-01) - both exchanges typically publish next year's calendar in
Nov/Dec. Jan-Aug 2027 weekday rows are inserted as PROVISIONAL (default
session, no known holidays) and flagged in `notes` for re-verification once
published.

Known limitation, deliberately not modeled: Muhurat trading (a short special
session on an otherwise-closed date, e.g. Diwali Laxmi Pujan when it falls on
a weekend) is left as is_trading_day=false with no encoded session window,
since exact Muhurat timings aren't in these circulars (published separately,
closer to the date, and vary year to year). This is safe for expected_bars:
any bars collected during a Muhurat window on a nominally non-trading day
just count as bonus (actual_bars > expected_bars=0 still resolves to OK).
"""
import datetime
import subprocess

NSE_SESSION = ("09:15", "15:30")
MCX_MORNING_ONLY = ("09:00", "17:00")   # New Year Day pattern

# MCX's evening close alternates with US Daylight Saving Time, to preserve
# overlap with NYMEX/COMEX/LME hours during Indian evening trading:
#   - 23:30 close during US DST (2nd Sunday of March -> 1st Sunday of Nov)
#   - 23:55 close otherwise
# Confirmed against real MCX-driven circulars:
#   Upstox, 2023-11-06 -> 23:55 (day after 2023's 1st Sunday of Nov, 11-05)
#   ICICI Direct, 2026-03-09 -> 23:30 (day after 2026's 2nd Sunday of Mar, 03-08)
# Computed rather than hardcoded per-year so the whole WINDOW_START..WINDOW_END
# range is covered without a manual table (and without repeating the bug this
# replaced, where MCX_EVENING_ONLY_OPEN festival rows had a fixed 23:30 close
# even on dates that actually fall in the 23:55 window).


def _nth_sunday(year, month, n):
    d0 = datetime.date(year, month, 1)
    first_sunday_offset = (6 - d0.weekday()) % 7  # weekday(): Mon=0..Sun=6
    return d0 + datetime.timedelta(days=first_sunday_offset + 7 * (n - 1))


def mcx_us_dst_active(d):
    """True if `d` falls in the US-DST window that drives MCX's evening close."""
    second_sunday_march = _nth_sunday(d.year, 3, 2)
    first_sunday_november = _nth_sunday(d.year, 11, 1)
    dst_start = second_sunday_march + datetime.timedelta(days=1)   # next trading day
    dst_end = first_sunday_november + datetime.timedelta(days=1)   # next trading day
    return dst_start <= d < dst_end


def mcx_evening_close(d):
    return "23:30" if mcx_us_dst_active(d) else "23:55"


def mcx_full_session(d):
    return ("09:00", mcx_evening_close(d))


def mcx_evening_only(d):
    return ("17:00", mcx_evening_close(d))

WINDOW_START = "2023-09-01"
WINDOW_END = "2027-08-31"
PROVISIONAL_FROM = "2027-01-01"  # first date with no published holiday calendar
PROVISIONAL_NOTE = "PROVISIONAL - holiday calendar not yet published as of 2026-09-01 population run; re-verify before relying on this date"

# All NSE holidays are full-day closures.
NSE_HOLIDAYS = {
    2023: [
        ("2023-09-19", "Ganesh Chaturthi"),
        ("2023-10-02", "Mahatma Gandhi Jayanti"),
        ("2023-10-24", "Dussehra"),
        ("2023-11-14", "Diwali-Balipratipada"),
        ("2023-11-27", "Gurunanak Jayanti"),
        ("2023-12-25", "Christmas"),
    ],
    2024: [
        ("2024-01-26", "Republic Day"),
        ("2024-03-08", "Mahashivratri"),
        ("2024-03-25", "Holi"),
        ("2024-03-29", "Good Friday"),
        ("2024-04-11", "Id-Ul-Fitr (Ramadan Eid)"),
        ("2024-04-17", "Shri Ram Navmi"),
        ("2024-05-01", "Maharashtra Day"),
        ("2024-06-17", "Bakri Id"),
        ("2024-07-17", "Moharram"),
        ("2024-08-15", "Independence Day/Parsi New Year"),
        ("2024-10-02", "Mahatma Gandhi Jayanti"),
        ("2024-11-01", "Diwali Laxmi Pujan"),
        ("2024-11-15", "Gurunanak Jayanti"),
        ("2024-12-25", "Christmas"),
    ],
    2025: [
        ("2025-02-26", "Mahashivratri"),
        ("2025-03-14", "Holi"),
        ("2025-03-31", "Id-Ul-Fitr (Ramadan Eid)"),
        ("2025-04-10", "Shri Mahavir Jayanti"),
        ("2025-04-14", "Dr. Baba Saheb Ambedkar Jayanti"),
        ("2025-04-18", "Good Friday"),
        ("2025-05-01", "Maharashtra Day"),
        ("2025-08-15", "Independence Day"),
        ("2025-08-27", "Ganesh Chaturthi"),
        ("2025-10-02", "Mahatma Gandhi Jayanti/Dussehra"),
        ("2025-10-21", "Diwali Laxmi Pujan"),
        ("2025-10-22", "Diwali-Balipratipada"),
        ("2025-11-05", "Prakash Gurpurb Sri Guru Nanak Dev"),
        ("2025-12-25", "Christmas"),
    ],
    2026: [
        ("2026-01-15", "Municipal Corporation Election - Maharashtra"),
        ("2026-01-26", "Republic Day"),
        ("2026-02-15", "Mahashivratri"),
        ("2026-03-03", "Holi"),
        ("2026-03-21", "Id-Ul-Fitr"),
        ("2026-03-26", "Ram Navami"),
        ("2026-03-31", "Mahavir Jayanti"),
        ("2026-04-03", "Good Friday"),
        ("2026-04-14", "Ambedkar Jayanti"),
        ("2026-05-01", "Maharashtra Day"),
        ("2026-05-28", "Bakri Id"),
        ("2026-06-26", "Moharram"),
        ("2026-08-15", "Independence Day"),
        ("2026-09-14", "Ganesh Chaturthi"),
        ("2026-10-02", "Gandhi Jayanti"),
        ("2026-10-20", "Dussehra"),
        ("2026-11-08", "Diwali Laxmi Pujan"),
        ("2026-11-10", "Diwali Balipratipada"),
        ("2026-11-24", "Guru Nanak Jayanti"),
        ("2026-12-25", "Christmas"),
    ],
}

MCX_FULL_CLOSURE = {
    2023: [
        ("2023-10-02", "Mahatma Gandhi Jayanti"),
        ("2023-12-25", "Christmas"),
    ],
    2024: [
        ("2024-01-26", "Republic Day"),
        ("2024-03-29", "Good Friday"),
        ("2024-08-15", "Independence Day"),
        ("2024-10-02", "Mahatma Gandhi Jayanti"),
        ("2024-12-25", "Christmas"),
    ],
    2025: [
        ("2025-04-18", "Good Friday"),
        ("2025-08-15", "Independence Day"),
        ("2025-10-02", "Mahatma Gandhi Jayanti/Dassera"),
        ("2025-10-21", "Diwali Laxmi Pujan"),
        ("2025-12-25", "Christmas"),
    ],
    2026: [
        ("2026-01-26", "Republic Day"),
        ("2026-04-03", "Good Friday"),
        ("2026-10-02", "Mahatma Gandhi Jayanti"),
        ("2026-12-25", "Christmas"),
    ],
}

MCX_MORNING_ONLY_OPEN = {
    2024: [("2024-01-01", "New Year Day")],
    2025: [("2025-01-01", "New Year Day")],
    2026: [("2026-01-01", "New Year Day")],
}

MCX_EVENING_ONLY_OPEN = {
    2023: [
        ("2023-09-19", "Ganesh Chaturthi"),
        ("2023-10-24", "Dassera"),
        ("2023-11-14", "Diwali Balipratipada"),
        ("2023-11-27", "Guru Nanak Jayanti"),
    ],
    2024: [
        ("2024-03-08", "Mahashivratri"),
        ("2024-03-25", "Holi (2nd day)"),
        ("2024-04-11", "Id-Ul-Fitr"),
        ("2024-04-17", "Shri Ram Navmi"),
        ("2024-05-01", "Maharashtra Day"),
        ("2024-06-17", "Bakri Id"),
        ("2024-07-17", "Moharram"),
        ("2024-11-01", "Diwali Laxmi Pujan (incl. Muhurat)"),
        ("2024-11-15", "Guru Nanak Jayanti"),
    ],
    2025: [
        ("2025-02-26", "Mahashivratri"),
        ("2025-03-14", "Holi (2nd day)"),
        ("2025-03-31", "Id-Ul-Fitr"),
        ("2025-04-10", "Shri Mahavir Jayanti"),
        ("2025-04-14", "Dr. Baba Saheb Ambedkar Jayanti"),
        ("2025-05-01", "Maharashtra Day"),
        ("2025-08-27", "Ganesh Chaturthi"),
        ("2025-10-22", "Diwali-Balipratipada"),
        ("2025-11-05", "Guru Nanak Jayanti"),
    ],
    2026: [
        ("2026-03-03", "Holi (2nd day)"),
        ("2026-03-26", "Shri Ram Navmi"),
        ("2026-03-31", "Shri Mahavir Jayanti"),
        ("2026-04-14", "Dr. Baba Saheb Ambedkar Jayanti"),
        ("2026-05-01", "Maharashtra Day"),
        ("2026-05-28", "Bakri Id"),
        ("2026-06-26", "Moharram"),
        ("2026-09-14", "Ganesh Chaturthi"),
        ("2026-10-20", "Dassera"),
        ("2026-11-10", "Diwali-Balipratipada"),
        ("2026-11-24", "Guru Nanak Jayanti"),
    ],
}


def daterange(start, end):
    import datetime
    d0 = datetime.date.fromisoformat(start)
    d1 = datetime.date.fromisoformat(end)
    d = d0
    while d <= d1:
        yield d
        d += datetime.timedelta(days=1)


def flatten(*year_dicts):
    out = {}
    for yd in year_dicts:
        for year, rows in yd.items():
            for date_str, desc in rows:
                out[date_str] = desc
    return out


def build_rows():
    nse_full = flatten(NSE_HOLIDAYS)
    mcx_full = flatten(MCX_FULL_CLOSURE)
    mcx_morning = flatten(MCX_MORNING_ONLY_OPEN)
    mcx_evening = flatten(MCX_EVENING_ONLY_OPEN)

    rows = []
    for d in daterange(WINDOW_START, WINDOW_END):
        date_str = d.isoformat()
        is_weekend = d.weekday() >= 5
        provisional = date_str >= PROVISIONAL_FROM

        # --- NSE ---
        # BSE mirrors NSE exactly for the equity/F&O segments (SENSEX
        # options included): same SEBI-mandated holiday list, same
        # 09:15-15:30 session -- confirmed against 2026 published
        # calendars, not assumed. So every NSE row gets an identical BSE
        # row rather than a second lookup table to keep in sync.
        if date_str in nse_full:
            rows.append(("NSE", date_str, False, None, None, nse_full[date_str]))
            rows.append(("BSE", date_str, False, None, None, nse_full[date_str]))
        elif is_weekend:
            rows.append(("NSE", date_str, False, None, None, None))
            rows.append(("BSE", date_str, False, None, None, None))
        else:
            note = PROVISIONAL_NOTE if provisional else None
            rows.append(("NSE", date_str, True, NSE_SESSION[0], NSE_SESSION[1], note))
            rows.append(("BSE", date_str, True, NSE_SESSION[0], NSE_SESSION[1], note))

        # --- MCX ---
        if date_str in mcx_full:
            rows.append(("MCX", date_str, False, None, None, mcx_full[date_str]))
        elif date_str in mcx_morning:
            s, e = MCX_MORNING_ONLY
            rows.append(("MCX", date_str, True, s, e, f"{mcx_morning[date_str]} - partial holiday, morning session only"))
        elif date_str in mcx_evening:
            s, e = mcx_evening_only(d)
            rows.append(("MCX", date_str, True, s, e, f"{mcx_evening[date_str]} - partial holiday, evening session only"))
        elif is_weekend:
            rows.append(("MCX", date_str, False, None, None, None))
        else:
            note = PROVISIONAL_NOTE if provisional else None
            s, e = mcx_full_session(d)
            rows.append(("MCX", date_str, True, s, e, note))

    return rows


def to_sql_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    escaped = str(v).replace("'", "''")
    return f"'{escaped}'"


def main():
    rows = build_rows()
    sql_path = "/tmp/trading_calendar_data.sql"
    with open(sql_path, "w") as f:
        f.write("BEGIN;\n")
        f.write("TRUNCATE trading_calendar;\n")
        batch = []
        for r in rows:
            values = "(" + ", ".join(to_sql_value(v) for v in r) + ")"
            batch.append(values)
            if len(batch) == 200:
                f.write(
                    "INSERT INTO trading_calendar (exchange, trade_date, is_trading_day, session_start, session_end, notes) VALUES\n"
                    + ",\n".join(batch)
                    + "\nON CONFLICT (exchange, trade_date) DO NOTHING;\n"
                )
                batch = []
        if batch:
            f.write(
                "INSERT INTO trading_calendar (exchange, trade_date, is_trading_day, session_start, session_end, notes) VALUES\n"
                + ",\n".join(batch)
                + "\nON CONFLICT (exchange, trade_date) DO NOTHING;\n"
            )
        f.write("COMMIT;\n")

    print(f"Wrote {len(rows)} rows to {sql_path}")

    result = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "fno_archive", "-f", sql_path],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise SystemExit(result.returncode)

    verify = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "fno_archive", "-c",
         "SELECT exchange, count(*), sum((is_trading_day)::int) AS trading_days, "
         "sum((NOT is_trading_day)::int) AS non_trading_days FROM trading_calendar GROUP BY exchange;"],
        capture_output=True, text=True
    )
    print(verify.stdout)

    import os
    os.remove(sql_path)


if __name__ == "__main__":
    main()
