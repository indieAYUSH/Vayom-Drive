import asyncio
import logging
import httpx
import re
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from .config import settings
from .db.connection import init_db, get_db
from .storage.client import storage_client
from .api.routes import router as api_router, perform_rebuild_index, load_persistent_storage_configs
from .api.auth import router as auth_router
from .api.workstation import router as workstation_router
from .workstation.watcher import workstation_watcher
from .transcoding.manager import transcoding_manager
from .streaming.router import router as streaming_router, root_router as streaming_root_router

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("vayom.main")

FRONTEND_DIST = settings.ROOT_DIR / "frontend" / "dist"

async def heartbeat_loop():
    """Periodic keep-alive heartbeat loop to prevent Hugging Face Spaces from idling."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
                try:
                    target_url = settings.PING_URL or f"http://127.0.0.1:{settings.PORT}/api/v1/health"
                    resp = await client.get(target_url)
                    logger.info(f"Self-ping heartbeat to {target_url} returned status: {resp.status_code}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug(f"Heartbeat ping: {e}")
    except asyncio.CancelledError:
        logger.info("Heartbeat loop cancelled.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup:
    logger.info("Initializing Vayom Drive Node...")
    await init_db()
    logger.info("Database initialized with WAL mode.")

    # Load persistent S3 & Nextcloud configs if present
    await load_persistent_storage_configs()

    # Load persistent vault key if stored
    from .crypto import vault_manager
    if vault_manager.load_stored_key():
        logger.info("Vault encryption unlocked automatically from stored workstation key.")

    # Connect to Telegram MTProto
    try:
        await storage_client.connect()
    except Exception as e:
        logger.warning(f"Could not connect to Telegram MTProto on startup: {e}")

    # Auto-rebuild SQLite index if catalog is empty and channel is connected
    try:
        async with get_db() as db:
            cur = await db.execute("SELECT COUNT(*) FROM files")
            count = (await cur.fetchone())[0]

        if count == 0 and storage_client._is_connected and settings.TELEGRAM_CHANNEL_ID:
            logger.info("SQLite catalog index is empty. Automatically rebuilding index from Telegram storage channel...")
            restored = await perform_rebuild_index(limit=5000)
            logger.info(f"Auto-index rebuild complete: restored {restored} envelope(s) from Telegram channel.")
    except Exception as e:
        logger.warning(f"Auto-rebuild on startup encountered: {e}")

    # Launch background keep-alive heartbeat task (F-7: only when a ping target is configured)
    heartbeat_task = None
    if settings.PING_URL:
        heartbeat_task = asyncio.create_task(heartbeat_loop())
    else:
        logger.info("Heartbeat loop disabled (PING_URL not set).")

    # Start Workstation Folder Watcher & Transcoding Manager
    await workstation_watcher.start()
    await transcoding_manager.start()

    yield

    # Shutdown:
    logger.info("Shutting down Vayom Drive...")
    await transcoding_manager.stop()
    await workstation_watcher.stop()
    if heartbeat_task:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
    await storage_client.disconnect()

app = FastAPI(
    title="Vayom Drive",
    description="Personal cloud storage powered by Telegram",
    version="1.0.0",
    lifespan=lifespan
)

# A-1: Replace wide-open CORS allow_origins=["*"] with explicit trusted origins
# Allows local web app, Vite dev servers, and Tauri desktop protocol
DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]
custom_origins = [o.strip() for o in getattr(settings, "CORS_ALLOWED_ORIGINS", []) if o.strip()]
trusted_origins = list(dict.fromkeys(DEFAULT_ALLOWED_ORIGINS + custom_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=trusted_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def enforce_api_security(request: Request, call_next):
    """
    A-1 Security Middleware:
    1. Origin Protection: Blocks cross-origin requests from untrusted external origins to /api/ routes (CSRF defense).
    2. Host Header Validation: Blocks external / spoofed host headers when bound to loopback.
    3. Token Authentication: Supports per-install local API token validation (or strict mode if configured).
    """
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/v1/s/"):
        origin = request.headers.get("origin")
        if origin:
            origin_clean = origin.rstrip("/").lower()
            trusted_clean = [o.rstrip("/").lower() for o in trusted_origins]
            is_trusted = (
                origin_clean in trusted_clean
                or re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin_clean) is not None
            )
            if not is_trusted:
                host = request.headers.get("host")
                if host and (origin_clean == f"http://{host.lower()}" or origin_clean == f"https://{host.lower()}"):
                    is_trusted = True

            if not is_trusted:
                logger.warning(f"A-1: Blocked cross-origin access from untrusted origin: {origin} to {path}")
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Forbidden: Cross-origin access from this origin is forbidden."}
                )

        # Host header validation: when bound to loopback, block external host spoofing
        if getattr(settings, "HOST", "127.0.0.1") in ("127.0.0.1", "localhost"):
            raw_host = request.headers.get("host", "").split(":")[0].strip().lower()
            if raw_host:
                is_valid_host = (
                    raw_host in ("127.0.0.1", "localhost", "::1", "testserver")
                    or raw_host.startswith("127.")
                    or any(raw_host == o.split("://")[-1].split(":")[0].lower() for o in trusted_origins)
                )
                if not is_valid_host:
                    logger.warning(f"A-1: Blocked request with untrusted host header: {raw_host}")
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Forbidden: Non-loopback Host header is denied."}
                    )

        # Optional strict per-install API token enforcement
        if getattr(settings, "REQUIRE_API_AUTH", False):
            auth_header = request.headers.get("authorization", "")
            api_key = request.headers.get("x-api-key", "")
            token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else auth_header.strip()
            if not token and api_key:
                token = api_key.strip()

            valid_tokens = {
                getattr(settings, "LOCAL_API_TOKEN", ""),
                getattr(settings, "SESSION_SECRET", ""),
                getattr(settings, "ADMIN_PASSWORD", "")
            }
            valid_tokens = {t for t in valid_tokens if t}

            if not token or token not in valid_tokens:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Unauthorized: Valid API authentication token required."}
                )

    return await call_next(request)

# Include REST API endpoints
app.include_router(api_router)
app.include_router(auth_router)
app.include_router(workstation_router)
app.include_router(streaming_router)
app.include_router(streaming_root_router)

@app.get("/s/{token}")
async def redirect_share(token: str):
    if not getattr(settings, "ENABLE_SHARE_LINKS", False):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Share links feature is disabled in v1")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/api/v1/s/{token}")

# Mount Frontend static assets and SPA fallback
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="API route not found")
        # Serve static file if exists (e.g. manifest.webmanifest, sw.js), else fallback to index.html
        potential_file = (FRONTEND_DIST / full_path).resolve()
        # F-1: is_relative_to blocks sibling-prefix traversal (e.g. /dist-evil) that startswith allowed
        if potential_file.is_file() and potential_file.is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(potential_file)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    async def no_frontend():
        return {"status": "Vayom Drive Backend Running (Build frontend to serve web app)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.HOST, port=settings.PORT, reload=False)
