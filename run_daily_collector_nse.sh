#!/bin/bash
# NSE/BSE half of the split collector: NIFTY + SENSEX only, scheduled at
# 16:00 IST -- after NSE/BSE's 15:30 close. Split from the original
# single 23:59 run so a NIFTY/SENSEX collection failure doesn't also
# risk CRUDEOIL's own separate, later window (and vice versa) -- see
# conversation. Log filename suffix ("_nse") matches
# daily_collector.py's own log_path_hint()'s derivation exactly, so
# crash-alert Log: lines point at the right file.
set -uo pipefail

PROJECT_DIR="/opt/fno-archive"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

TRADE_DATE=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/collector_${TRADE_DATE}_nse.log"

{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run starting for trade_date=$TRADE_DATE (NIFTY,SENSEX) ==="
    cd "$PROJECT_DIR" && python3 -u daily_collector.py --date "$TRADE_DATE" --underlyings NIFTY,SENSEX
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run FINISHED OK, exit_code=0 ==="
    else
        echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') collector run FAILED, exit_code=$EXIT_CODE ==="
    fi
    exit "$EXIT_CODE"
} >> "$LOG_FILE" 2>&1
