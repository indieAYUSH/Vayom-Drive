#!/usr/bin/env bash
set -e

# build_sidecar.sh
# Bundles backend/main.py into a standalone PyInstaller binary for Tauri externalBin.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Building Vayom Drive Backend Standalone Sidecar ==="

# Determine Python command (Git Bash on Windows has no .venv/bin and often no python3)
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    PYTHON="python"
fi

if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "PyInstaller not found. Installing via pip..."
    "$PYTHON" -m pip install pyinstaller
fi

# Detect platform target triple
ARCH="$(uname -m)"
# Rust uses aarch64, while macOS uname reports arm64.
if [ "$ARCH" = "arm64" ]; then
    ARCH="aarch64"
fi
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
if [ "$OS" = "linux" ]; then
    TARGET="${ARCH}-unknown-linux-gnu"
elif [ "$OS" = "darwin" ]; then
    TARGET="${ARCH}-apple-darwin"
else
    TARGET="${ARCH}-pc-windows-msvc"
fi

echo "Target triple: $TARGET"

mkdir -p "src-tauri/binaries"
DIST_DIR="dist_sidecar"
BUILD_DIR="build_sidecar"
rm -rf "$DIST_DIR" "$BUILD_DIR"

# PyInstaller must import backend.main as a package. Running backend/main.py
# directly breaks its relative imports, so use a tiny top-level launcher.
ENTRYPOINT="$PROJECT_ROOT/dist_sidecar_entry.py"
APP_ENV_DIR="$PROJECT_ROOT/build_sidecar_env"
APP_ENV_FILE="$APP_ENV_DIR/telegram_app.env"
rm -rf "$APP_ENV_DIR"
mkdir -p "$APP_ENV_DIR"
trap 'rm -f "$ENTRYPOINT"; rm -rf "$APP_ENV_DIR"' EXIT
cat > "$ENTRYPOINT" <<'PY'
import multiprocessing
import uvicorn
from backend.main import app
from backend.config import settings

if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, reload=False)
PY

# PyInstaller --add-data uses ':' on Linux/macOS and ';' on Windows.
if [ "$OS" = "linux" ] || [ "$OS" = "darwin" ]; then
    DATA_SEP=":"
else
    DATA_SEP=";"
fi

# PyInstaller is a native executable. Under Git Bash on Windows the MSYS paths
# (/d/...) must be converted before they reach --paths/--add-data.
PROJECT_ARG="$PROJECT_ROOT"
SCHEMA_ARG="$PROJECT_ROOT/backend/db/schema.sql"
APP_ENV_ARG="$APP_ENV_FILE"
if command -v cygpath >/dev/null 2>&1; then
    PROJECT_ARG="$(cygpath -w "$PROJECT_ROOT")"
    SCHEMA_ARG="$(cygpath -w "$PROJECT_ROOT/backend/db/schema.sql")"
    APP_ENV_ARG="$(cygpath -w "$APP_ENV_FILE")"
fi

# --- Telegram application credentials for packaged installs -----------------
# api_id/api_hash identify the *application*, not the user, and the packaged
# sidecar cannot work without them. Embed ONLY those two values: the rest of
# .env contains local secrets (admin password, session secret, vault keys) and
# must never be shipped inside an installer.
echo "Collecting Telegram application credentials..."
"$PYTHON" - "$APP_ENV_FILE" <<'PY'
import os
import sys
from pathlib import Path

try:
    from dotenv import dotenv_values
    file_values = dotenv_values(".env") if Path(".env").is_file() else {}
except Exception:
    file_values = {}

api_id = (os.getenv("TELEGRAM_API_ID") or file_values.get("TELEGRAM_API_ID") or "").strip()
api_hash = (os.getenv("TELEGRAM_API_HASH") or file_values.get("TELEGRAM_API_HASH") or "").strip()
target = Path(sys.argv[1])

if not api_id or not api_hash:
    target.write_text("", encoding="utf-8")
    print("WARNING: TELEGRAM_API_ID / TELEGRAM_API_HASH are not set.")
    print("WARNING: this sidecar will run in mock/offline mode and QR/bot login will be disabled.")
    print("WARNING: set them in .env or export them before building a release.")
    if os.getenv("CI", "").strip().lower() in ("1", "true", "yes"):
        sys.exit("Refusing to build a release sidecar without Telegram application credentials.")
else:
    target.write_text(
        f"TELEGRAM_API_ID={api_id}\nTELEGRAM_API_HASH={api_hash}\n",
        encoding="utf-8",
    )
    os.chmod(target, 0o600)
    print("Embedding Telegram application credentials (api_id/api_hash only).")
PY

echo "Running PyInstaller..."
"$PYTHON" -m PyInstaller \
    --name "vayom-backend" \
    --onefile \
    --clean \
    --paths "$PROJECT_ARG" \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR" \
    --add-data "${SCHEMA_ARG}${DATA_SEP}backend/db" \
    --add-data "${APP_ENV_ARG}${DATA_SEP}." \
    --hidden-import "backend.main" \
    --hidden-import "uvicorn" \
    --hidden-import "uvicorn.logging" \
    --hidden-import "uvicorn.loops" \
    --hidden-import "uvicorn.loops.auto" \
    --hidden-import "uvicorn.protocols" \
    --hidden-import "uvicorn.protocols.http" \
    --hidden-import "uvicorn.protocols.http.auto" \
    --hidden-import "uvicorn.protocols.websockets" \
    --hidden-import "uvicorn.protocols.websockets.auto" \
    --hidden-import "uvicorn.lifespan" \
    --hidden-import "uvicorn.lifespan.on" \
    --hidden-import "aiosqlite" \
    --hidden-import "telethon" \
    --hidden-import "cryptography" \
    --hidden-import "cryptg" \
    --hidden-import "PIL" \
    --hidden-import "fpdf" \
    --hidden-import "dotenv" \
    --copy-metadata "uvicorn" \
    --copy-metadata "fastapi" \
    "$ENTRYPOINT"

echo "Copying binary to Tauri externalBin locations..."
if [ -f "$DIST_DIR/vayom-backend.exe" ]; then
    cp "$DIST_DIR/vayom-backend.exe" "src-tauri/binaries/vayom-backend.exe"
    cp "$DIST_DIR/vayom-backend.exe" "src-tauri/binaries/vayom-backend-$TARGET.exe"
fi
if [ -f "$DIST_DIR/vayom-backend" ]; then
    cp "$DIST_DIR/vayom-backend" "src-tauri/binaries/vayom-backend"
    cp "$DIST_DIR/vayom-backend" "src-tauri/binaries/vayom-backend-$TARGET"
    chmod +x "src-tauri/binaries/vayom-backend" "src-tauri/binaries/vayom-backend-$TARGET"
fi

# Keep a copy next to the dev sidecar too, so `tauri dev` and direct launches
# from src-tauri/binaries resolve the credentials even if the embedded copy is
# unavailable (e.g. an older onefile extraction cache).
cp "$APP_ENV_FILE" "src-tauri/binaries/telegram_app.env"
chmod 600 "src-tauri/binaries/telegram_app.env" 2>/dev/null || true

echo "Successfully built and packaged Vayom Drive sidecar binary!"
echo "Locations:"
echo " - src-tauri/binaries/vayom-backend"
echo " - src-tauri/binaries/vayom-backend-$TARGET"
echo " - src-tauri/binaries/telegram_app.env"
