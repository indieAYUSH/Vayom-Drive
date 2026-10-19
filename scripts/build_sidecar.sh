#!/usr/bin/env bash
set -e

# build_sidecar.sh
# Bundles backend/main.py into a standalone PyInstaller binary for Tauri externalBin.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Building Vayom Drive Backend Standalone Sidecar ==="

# Determine Python command
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
    PYINSTALLER=".venv/bin/pyinstaller"
else
    PYTHON="python3"
    PYINSTALLER="pyinstaller"
fi

if ! command -v "$PYINSTALLER" >/dev/null 2>&1 && [ ! -f "$PYINSTALLER" ]; then
    echo "PyInstaller not found. Installing via uv/pip..."
    uv pip install pyinstaller || pip install pyinstaller
fi

# Detect platform target triple
ARCH="$(uname -m)"
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

echo "Running PyInstaller..."
"$PYINSTALLER" \
    --name "vayom-backend" \
    --onefile \
    --clean \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR" \
    --hidden-import "uvicorn" \
    --hidden-import "uvicorn.logging" \
    --hidden-import "uvicorn.loops" \
    --hidden-import "uvicorn.loops.auto" \
    --hidden-import "uvicorn.protocols" \
    --hidden-import "uvicorn.protocols.http" \
    --hidden-import "uvicorn.protocols.http.auto" \
    --hidden-import "uvicorn.protocols.websockets" \
    --hidden-import "uvicorn.protocols.websockets.auto" \
    --hidden-import "uvicorn.lifespans" \
    --hidden-import "uvicorn.lifespans.auto" \
    --hidden-import "aiosqlite" \
    --hidden-import "telethon" \
    --hidden-import "cryptography" \
    --hidden-import "cryptg" \
    --hidden-import "PIL" \
    --hidden-import "fpdf2" \
    --hidden-import "dotenv" \
    --copy-metadata "uvicorn" \
    --copy-metadata "fastapi" \
    backend/main.py

echo "Copying binary to Tauri externalBin locations..."
cp "$DIST_DIR/vayom-backend" "src-tauri/binaries/vayom-backend"
cp "$DIST_DIR/vayom-backend" "src-tauri/binaries/vayom-backend-$TARGET"
chmod +x "src-tauri/binaries/vayom-backend" "src-tauri/binaries/vayom-backend-$TARGET"

echo "Successfully built and packaged Vayom Drive sidecar binary!"
echo "Locations:"
echo " - src-tauri/binaries/vayom-backend"
echo " - src-tauri/binaries/vayom-backend-$TARGET"
