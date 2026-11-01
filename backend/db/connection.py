import aiosqlite
import asyncio
import inspect
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Set
from ..config import settings

logger = logging.getLogger("vayom.db")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Track active connections so they can be deterministically closed on shutdown
_active_connections: Set[aiosqlite.Connection] = set()


def _harden_db_permissions():
    """Best-effort owner-only permissions for the SQLite DB and its WAL files."""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(settings.DB_PATH) + suffix)
        if candidate.exists():
            try:
                os.chmod(candidate, 0o600)
            except OSError:
                pass


async def _safe_close(conn: aiosqlite.Connection) -> None:
    """
    Deterministically closes an aiosqlite connection and ensures its background thread stops.
    Protects against task cancellation so that closing is never aborted midway,
    preventing active worker threads from outliving the event loop.
    """
    async def _do_close():
        try:
            res = conn.close()
            if inspect.isawaitable(res):
                await res
        except Exception as e:
            logger.debug(f"Error while closing SQLite connection: {e}")
        # Ensure the worker thread has completely stopped
        if hasattr(conn, "_thread") and conn._thread and conn._thread.is_alive():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, conn._thread.join, 1.0)
            except Exception:
                pass

    close_task = asyncio.create_task(_do_close())
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError:
        # Awaiting task was cancelled; wait for close_task to finish so the thread terminates cleanly
        await asyncio.wait([close_task])
        raise


@asynccontextmanager
async def get_db():
    """
    Async context manager providing a SQLite connection configured for WAL mode.
    Guarantees deterministic closing even if caller tasks are cancelled or encounter exceptions.
    """
    conn = None
    try:
        conn = await aiosqlite.connect(settings.DB_PATH)
        conn.row_factory = aiosqlite.Row
        _active_connections.add(conn)

        # Enable Write-Ahead Logging for high-concurrency performance
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.execute("PRAGMA busy_timeout=5000;")
        _harden_db_permissions()

        yield conn
    finally:
        if conn is not None:
            _active_connections.discard(conn)
            await _safe_close(conn)


async def checkpoint_wal() -> None:
    """
    Graceful WAL checkpoint helper running PRAGMA wal_checkpoint(PASSIVE).
    Flushes pending WAL frames to the database file on shutdown without blocking active readers/writers.
    """
    db_path_str = str(settings.DB_PATH)
    if db_path_str != ":memory:" and not Path(db_path_str).exists():
        return

    try:
        async with get_db() as db:
            cursor = await db.execute("PRAGMA wal_checkpoint(PASSIVE);")
            result = await cursor.fetchone()
            if result:
                logger.info(
                    f"SQLite WAL checkpoint completed (busy={result[0]}, log={result[1]}, checkpointed={result[2]})."
                )
            else:
                logger.info("SQLite WAL checkpoint completed.")
    except Exception as e:
        logger.warning(f"Graceful SQLite WAL checkpoint failed: {e}")


async def close_db_connections() -> None:
    """
    Deterministically closes all tracked open SQLite connections.
    Called during server shutdown to guarantee no lingering threads access the closed event loop.
    """
    while _active_connections:
        conns = list(_active_connections)
        _active_connections.clear()
        for conn in conns:
            try:
                await _safe_close(conn)
            except Exception as e:
                logger.debug(f"Error in close_db_connections: {e}")

async def init_db():
    """Initializes the database schema if not already present, and runs non-destructive migrations."""
    settings.ensure_directories()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    async with get_db() as db:
        # Non-destructive migrations for existing databases before creating indexes
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
        if await cursor.fetchone():
            cursor = await db.execute("PRAGMA table_info(files)")
            columns = [row["name"] for row in await cursor.fetchall()]

            if "is_split" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN is_split INTEGER NOT NULL DEFAULT 0")
            if "total_parts" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN total_parts INTEGER NOT NULL DEFAULT 1")
            if "has_thumbnail" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN has_thumbnail INTEGER NOT NULL DEFAULT 0")
            if "thumbnail_path" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN thumbnail_path TEXT")
            if "exif_json" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN exif_json TEXT")
            if "taken_at" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN taken_at TIMESTAMP")
            if "latitude" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN latitude REAL")
            if "longitude" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN longitude REAL")
            if "duration_seconds" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN duration_seconds REAL")
            if "upload_status" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN upload_status TEXT NOT NULL DEFAULT 'completed'")
            if "nonce" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN nonce TEXT")
            if "encryption" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN encryption TEXT NOT NULL DEFAULT 'none'")
            if "is_deleted" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
            if "deleted_at" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN deleted_at TIMESTAMP")
            if "kind" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN kind TEXT NOT NULL DEFAULT 'original'")
            if "proxy_of" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN proxy_of TEXT REFERENCES files(id) ON DELETE CASCADE")
            if "proxy_profile" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN proxy_profile TEXT")
            if "proxy_status" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN proxy_status TEXT NOT NULL DEFAULT 'none'")
            if "video_bitrate_bps" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN video_bitrate_bps INTEGER")
            if "width" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN width INTEGER")
            if "height" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN height INTEGER")
            if "codec" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN codec TEXT")
            if "container" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN container TEXT")
            if "duration_s" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN duration_s REAL")
            if "storage_backend" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'telegram'")
            if "created_at" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
            if "updated_at" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
            await db.commit()

        # Non-destructive migrations for file_parts
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_parts'")
        if await cursor.fetchone():
            cursor = await db.execute("PRAGMA table_info(file_parts)")
            p_cols = [row["name"] for row in await cursor.fetchall()]
            if "telegram_message_id" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN telegram_message_id INTEGER DEFAULT 0")
            if "telegram_channel_id" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN telegram_channel_id TEXT NOT NULL DEFAULT ''")
            if "telegram_file_id" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN telegram_file_id TEXT")
            if "telegram_access_hash" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN telegram_access_hash TEXT")
            if "upload_status" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN upload_status TEXT NOT NULL DEFAULT 'confirmed'")
            if "storage_backend" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'telegram'")
            if "storage_uri" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN storage_uri TEXT NOT NULL DEFAULT ''")
            if "created_at" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
            await db.commit()

        await db.executescript(schema_sql)
        await db.commit()
