#!/bin/bash
# MCX half of the split collector: CRUDEOIL only, scheduled at 23:56 IST
# -- after MCX's DST-adjusted 23:55 close (see populate_calendar.py's
# mcx_evening_close()). Tight ~1 min buffer, accepted: CRUDEOIL's
# monthly expiry is lower-stakes than NIFTY/SENSEX's weekly cadence.
# Log filename suffix ("_mcx") matches daily_collector.py's own
# log_path_hint()'s derivation exactly, so crash-alert Log: lines point
# at the right file.
set -uo pipefail

PROJECT_DIR="/opt/fno-archive"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

TRADE_DATE=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/collector_${TRADE_DATE}_mcx.log"

{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run starting for trade_date=$TRADE_DATE (CRUDEOIL) ==="
    cd "$PROJECT_DIR" && python3 -u daily_collector.py --date "$TRADE_DATE" --underlyings CRUDEOIL
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run FINISHED OK, exit_code=0 ==="
    else
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run FAILED, exit_code=$EXIT_CODE ==="
    fi
    exit "$EXIT_CODE"
} >> "$LOG_FILE" 2>&1
