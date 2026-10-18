import aiosqlite
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from ..config import settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

@asynccontextmanager
async def get_db():
    conn = await aiosqlite.connect(settings.DB_PATH)
    conn.row_factory = aiosqlite.Row
    try:
        # Enable Write-Ahead Logging for high-concurrency performance
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.execute("PRAGMA busy_timeout=5000;")
        yield conn
    finally:
        await conn.close()

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

            if "upload_status" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN upload_status TEXT NOT NULL DEFAULT 'completed'")
            if "nonce" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN nonce TEXT")
            if "encryption" not in columns:
                await db.execute("ALTER TABLE files ADD COLUMN encryption TEXT NOT NULL DEFAULT 'none'")
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
            await db.commit()

        # Non-destructive migrations for file_parts
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_parts'")
        if await cursor.fetchone():
            cursor = await db.execute("PRAGMA table_info(file_parts)")
            p_cols = [row["name"] for row in await cursor.fetchall()]
            if "storage_backend" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'telegram'")
            if "storage_uri" not in p_cols:
                await db.execute("ALTER TABLE file_parts ADD COLUMN storage_uri TEXT NOT NULL DEFAULT ''")
            await db.commit()

        await db.executescript(schema_sql)
        await db.commit()
