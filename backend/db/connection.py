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
    """Initializes the database schema if not already present."""
    settings.ensure_directories()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    async with get_db() as db:
        await db.executescript(schema_sql)
        await db.commit()
