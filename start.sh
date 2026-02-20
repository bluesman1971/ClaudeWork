#!/bin/bash
# Trip Guide App - Start both servers

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

BACKEND_PORT=5001
FRONTEND_PORT=5000
LOG_FILE="/tmp/trip-guide-backend.log"

# --- Cleanup on exit ---
cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    exit 0
}
trap cleanup INT TERM

# --- Kill anything already on these ports ---
for PORT in $BACKEND_PORT $FRONTEND_PORT; do
    EXISTING=$(lsof -ti :$PORT 2>/dev/null || true)
    if [ -n "$EXISTING" ]; then
        echo "Stopping existing process on port $PORT (PID $EXISTING)..."
        kill "$EXISTING" 2>/dev/null || true
        sleep 0.5
    fi
done

# --- Activate virtualenv ---
if [ -f "$DIR/venv/bin/activate" ]; then
    source "$DIR/venv/bin/activate"
else
    echo "ERROR: virtualenv not found at $DIR/venv"
    echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# --- Check .env exists ---
if [ ! -f "$DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example and add your ANTHROPIC_API_KEY."
    exit 1
fi

# --- Start Flask backend ---
echo "Starting backend  → http://localhost:$BACKEND_PORT"
python "$DIR/app.py" > "$LOG_FILE" 2>&1 &
BACKEND_PID=$!

# Wait for backend to be ready (up to 10s)
echo -n "Waiting for backend"
for i in $(seq 1 20); do
    if curl -s "http://127.0.0.1:$BACKEND_PORT/health" > /dev/null 2>&1; then
        echo " ✓"
        break
    fi
    echo -n "."
    sleep 0.5
    if [ $i -eq 20 ]; then
        echo " FAILED"
        echo "Backend did not start. Check logs: $LOG_FILE"
        cat "$LOG_FILE"
        exit 1
    fi
done

# --- Start frontend static server ---
echo "Starting frontend → http://localhost:$FRONTEND_PORT"
python3 -m http.server $FRONTEND_PORT --directory "$DIR" > /dev/null 2>&1 &
FRONTEND_PID=$!

# --- Done ---
echo ""
echo "========================================"
echo "  Trip Guide App is running!"
echo "  Open: http://localhost:$FRONTEND_PORT"
echo "  Press Ctrl+C to stop both servers"
echo "========================================"
echo ""

# Keep script alive
wait
