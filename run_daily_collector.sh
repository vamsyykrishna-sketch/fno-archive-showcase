#!/bin/bash
# Wrapper for the nightly cron job: runs daily_collector.py for the current
# trade_date and logs timestamped output so a failure is visible without
# checking manually. Scheduled at 23:59 IST -- deliberately after both of
# MCX's possible evening closes (23:30 DST / 23:55 non-DST, see
# populate_calendar.py's mcx_evening_close()) and NSE/BSE's 15:30 close.
set -uo pipefail

PROJECT_DIR="/opt/fno-archive"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

TRADE_DATE=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/collector_${TRADE_DATE}.log"

{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run starting for trade_date=$TRADE_DATE ==="
    cd "$PROJECT_DIR" && python3 -u daily_collector.py --date "$TRADE_DATE"
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run FINISHED OK, exit_code=0 ==="
    else
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run FAILED, exit_code=$EXIT_CODE ==="
    fi
    exit "$EXIT_CODE"
} >> "$LOG_FILE" 2>&1
