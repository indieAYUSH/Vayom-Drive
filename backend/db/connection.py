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
            await db.commit()

        await db.executescript(schema_sql)
        await db.commit()
