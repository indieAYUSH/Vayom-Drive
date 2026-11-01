# ==============================================================================
# 🌌 Vayom Drive - Production Multi-Stage Dockerfile
# Stage 1: Build the React 18 / Vite / TypeScript Frontend
# Stage 2: Python 3.12 Slim Runtime with FFmpeg & SQLite WAL
# ==============================================================================

# --- Stage 1: Frontend Build ---
FROM node:20-alpine AS frontend-builder
WORKDIR /build

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# --- Stage 2: Production Python Runtime ---
FROM python:3.12-slim

# Install system dependencies (FFmpeg for video thumbnails, proxies, and streaming)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python backend dependencies
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r backend/requirements.txt

# Copy backend source
COPY backend/ ./backend

# Copy built frontend distribution from Stage 1 into frontend/dist
COPY --from=frontend-builder /build/dist ./frontend/dist

# Ensure persistent data directory structure exists
RUN mkdir -p /app/data /app/data/thumbnails /app/data/temp

# Environment variables with sensible production defaults
ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

# Expose unified Web UI and API port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/v1/health || exit 1

# Start Vayom Drive Node
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
