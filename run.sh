#!/usr/bin/env bash
set -e

echo "🌌 Starting Vayom Drive..."

# 1. Setup Backend Environment
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# 2. Check for .env file
if [ ! -f ".env" ]; then
    echo "⚠️  No .env file found. Creating a starter .env..."
    cat <<EOF > .env
PORT=8000
DEFAULT_STORAGE_BACKEND=telegram
UPLOAD_TEMP_DIR=./data/temp
THUMBNAIL_DIR=./data/thumbnails
DATA_DIR=./data
EOF
fi

# 3. Start Backend in Background
echo "Starting backend service on port 8000..."
uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# Trap Ctrl+C to cleanly kill backend
cleanup() {
    echo ""
    echo "Shutting down Vayom Drive..."
    kill $BACKEND_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# 4. Start Frontend
echo "Starting frontend dev server..."
cd frontend
if [ ! -d "node_modules" ]; then
    echo "Installing frontend dependencies..."
    npm install
fi

npm run dev

# Wait for cleanup
cleanup
