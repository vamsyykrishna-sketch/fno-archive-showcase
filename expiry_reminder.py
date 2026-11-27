#!/usr/bin/env python3
"""
Expiry reminder -- deliberately a SEPARATE script/cron entry from
daily_collector.py, not a check bundled into it. The whole point of this
reminder is to catch exactly the failure modes the collector itself is
prone to (expired token, box downtime, a crash) -- bundling it into the
collector's own process would mean the reminder goes dark exactly when
it's needed most, the one time it would actually matter. For the same
reason, run_psql() below is a small, deliberate duplicate of
daily_collector.py's own helper, not an import from it -- a future bug
in the collector should never be able to silently break this too.

Accepted limitation, not something to build around today: if the box
itself is off at 09:00 IST (this script's own cron time), the reminder
won't fire either. No external watchdog for that.

Fires two reminders per expiry cycle -- at exactly 2 days out and again
at exactly 1 day out -- not a continuous "within N days" nag. Runs once
daily via cron; each run's date arithmetic either matches or doesn't, so
no persisted "already sent" state is needed.
"""
import datetime
import subprocess

from telegram_alerts import alert_expiry_reminder

DB = "fno_archive"
PSQL = ["sudo", "-u", "postgres", "psql", "-d", DB]

REMINDER_DAYS = (2, 1)


def run_psql(sql):
    cmd = list(PSQL) + ["-t", "-A", "-F", "|", "-c", sql]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr}")
    return result.stdout


def get_nearest_expiries():
    """Returns {underlying: expiry_date} for each of NIFTY/SENSEX/CRUDEOIL's
    nearest unexpired expiry. Same near_term logic as
    select_active_contracts.sql, simplified to one row per underlying --
    this only needs the dates, not the per-contract rows."""
    sql = """
    SELECT i.symbol, MIN(c.expiry_date)
    FROM contracts c
    JOIN instruments i ON i.instrument_id = c.instrument_id
    WHERE i.symbol IN ('NIFTY', 'SENSEX', 'CRUDEOIL')
      AND c.expiry_date >= CURRENT_DATE
    GROUP BY i.symbol;
    """
    out = run_psql(sql)
    expiries = {}
    for line in out.strip().splitlines():
        if not line:
            continue
        underlying, expiry_str = line.split("|")
        expiries[underlying] = datetime.date.fromisoformat(expiry_str)
    return expiries


def main():
    today = datetime.date.today()
    expiries = get_nearest_expiries()

    for underlying, expiry_date in expiries.items():
        days_until = (expiry_date - today).days
        if days_until in REMINDER_DAYS:
            print(f"{underlying}: expiry {expiry_date} is {days_until} day(s) "
                  f"away -- sending reminder")
            alert_expiry_reminder(underlying, expiry_date, days_until)
        else:
            print(f"{underlying}: expiry {expiry_date} is {days_until} day(s) "
                  f"away -- no reminder due")


if __name__ == "__main__":
    main()
