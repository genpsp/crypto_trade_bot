#!/bin/bash
# GMO SOL_JPY orderbook collector をバックグラウンドで起動する
# Usage: bash research/scripts/start_orderbook_collector.sh
# 停止: kill $(cat research/data/raw/orderbook/.collector.pid)

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PID_FILE="$REPO_DIR/research/data/raw/orderbook/.collector.pid"
LOG_FILE="$REPO_DIR/research/data/raw/orderbook/collector.log"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Collector already running (PID $(cat "$PID_FILE"))"
    exit 0
fi

mkdir -p "$REPO_DIR/research/data/raw/orderbook"

source "$REPO_DIR/.venv/bin/activate"

nohup python -m research.scripts.collect_gmo_orderbook \
    --output-dir "$REPO_DIR/research/data/raw/orderbook" \
    --interval 15 \
    --depth 10 \
    >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "Collector started (PID $!), log: $LOG_FILE"
