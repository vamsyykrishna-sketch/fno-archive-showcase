#!/bin/bash
# Wrapper for the daily expiry-reminder check: deliberately a SEPARATE
# cron entry from run_daily_collector.sh, not bundled into it -- see
# expiry_reminder.py's own docstring for why. Scheduled at 09:00 IST,
# right after market open: gives the whole day to act on a reminder,
# unlike the collector's own 23:59 slot where nothing is left to do.
set -uo pipefail

PROJECT_DIR="/opt/fno-archive"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/expiry_reminder_${TODAY}.log"

{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') expiry reminder check starting ==="
    cd "$PROJECT_DIR" && python3 -u expiry_reminder.py
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') expiry reminder check FINISHED OK, exit_code=0 ==="
    else
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') expiry reminder check FAILED, exit_code=$EXIT_CODE ==="
    fi
    exit "$EXIT_CODE"
} >> "$LOG_FILE" 2>&1
