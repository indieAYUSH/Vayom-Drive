import asyncio
import logging
import httpx
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .config import settings
from .db.connection import init_db
from .storage.client import storage_client
from .api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("vayom.main")

FRONTEND_DIST = settings.ROOT_DIR / "frontend" / "dist"

async def heartbeat_loop():
    """Periodic keep-alive heartbeat loop to prevent Hugging Face Spaces from idling."""
    client = httpx.AsyncClient(timeout=10.0)
    while True:
        await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
        try:
            target_url = settings.PING_URL or f"http://127.0.0.1:{settings.PORT}/api/v1/health"
            resp = await client.get(target_url)
            logger.info(f"Self-ping heartbeat to {target_url} returned status: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Heartbeat ping: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup:
    logger.info("Initializing Vayom Drive Node...")
    await init_db()
    logger.info("Database initialized with WAL mode.")

    # Connect to Telegram MTProto
    try:
        await storage_client.connect()
    except Exception as e:
        logger.warning(f"Could not connect to Telegram MTProto on startup: {e}")

    # Launch background keep-alive heartbeat task
    heartbeat_task = asyncio.create_task(heartbeat_loop())

    yield

    # Shutdown:
    logger.info("Shutting down Vayom Drive...")
    heartbeat_task.cancel()
    if storage_client.client:
        await storage_client.client.disconnect()

app = FastAPI(
    title="Vayom Drive",
    description="Personal cloud storage powered by Telegram",
    version="1.0.0",
    lifespan=lifespan
)

# Include REST API endpoints
app.include_router(api_router)

@app.get("/s/{token}")
async def redirect_share(token: str):
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
        potential_file = FRONTEND_DIST / full_path
        if potential_file.is_file():
            return FileResponse(potential_file)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    async def no_frontend():
        return {"status": "Vayom Drive Backend Running (Build frontend to serve web app)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.HOST, port=settings.PORT, reload=False)
