"""
Tests for SQLite connection safety, deterministic thread termination on cancellation,
WAL checkpointing, connection cleanup, and database schema migrations.
"""
import asyncio
import os
import aiosqlite
import pytest
from pathlib import Path
from backend.config import settings
from backend.db.connection import (
    get_db,
    init_db,
    checkpoint_wal,
    close_db_connections,
    _active_connections,
    SCHEMA_PATH,
)
from backend.main import lifespan, app


@pytest.mark.asyncio
async def test_init_db_creates_all_tables():
    """Verify that init_db creates all core tables defined in schema.sql."""
    await init_db()
    async with get_db() as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cur.fetchall()}

    expected_tables = {
        "files",
        "file_parts",
        "albums",
        "file_albums",
        "tags",
        "file_tags",
        "share_links",
        "watch_folders",
        "watch_folder_events",
        "system_settings",
    }
    assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"


@pytest.mark.asyncio
async def test_init_db_migrates_legacy_files_table(tmp_path, monkeypatch):
    """Verify that init_db safely migrates existing files table lacking new columns."""
    custom_db = tmp_path / "legacy_files.db"
    monkeypatch.setattr(settings, "DB_PATH", custom_db)

    # Create a minimal legacy files table
    conn = await aiosqlite.connect(custom_db)
    await conn.execute("""
        CREATE TABLE files (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            file_category TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL
        );
    """)
    await conn.commit()
    await conn.close()

    # Run init_db migration
    await init_db()

    async with get_db() as db:
        cur = await db.execute("PRAGMA table_info(files)")
        columns = {row["name"] for row in await cur.fetchall()}

    required_columns = {
        "upload_status",
        "nonce",
        "encryption",
        "kind",
        "proxy_of",
        "proxy_profile",
        "proxy_status",
        "video_bitrate_bps",
        "width",
        "height",
        "codec",
        "container",
        "duration_s",
        "storage_backend",
    }
    assert required_columns.issubset(columns), f"Missing migrated columns: {required_columns - columns}"


@pytest.mark.asyncio
async def test_init_db_migrates_legacy_file_parts_table(tmp_path, monkeypatch):
    """Verify that init_db safely migrates existing file_parts table lacking storage_backend/storage_uri."""
    custom_db = tmp_path / "legacy_parts.db"
    monkeypatch.setattr(settings, "DB_PATH", custom_db)

    conn = await aiosqlite.connect(custom_db)
    await conn.execute("""
        CREATE TABLE file_parts (
            id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            part_index INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            part_size_bytes INTEGER NOT NULL,
            part_sha256 TEXT NOT NULL
        );
    """)
    await conn.commit()
    await conn.close()

    await init_db()

    async with get_db() as db:
        cur = await db.execute("PRAGMA table_info(file_parts)")
        columns = {row["name"] for row in await cur.fetchall()}

    assert "storage_backend" in columns
    assert "storage_uri" in columns


@pytest.mark.asyncio
async def test_get_db_deterministic_close_on_cancellation():
    """Verify get_db closes connection and stops worker thread deterministically when cancelled."""
    conn_holder = []

    async def worker():
        async with get_db() as db:
            conn_holder.append(db)
            await asyncio.sleep(60.0)

    task = asyncio.create_task(worker())
    await asyncio.sleep(0.05)  # Let worker acquire connection
    assert len(conn_holder) == 1
    conn = conn_holder[0]
    assert conn in _active_connections

    # Cancel the worker task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Connection must be removed from active registry and thread terminated
    assert conn not in _active_connections
    if hasattr(conn, "_thread") and conn._thread:
        assert not conn._thread.is_alive(), "aiosqlite worker thread was not joined after cancellation!"


@pytest.mark.asyncio
async def test_get_db_deterministic_close_on_exception():
    """Verify get_db closes connection and removes it from tracking on exception."""
    conn_holder = []

    with pytest.raises(ValueError, match="simulated failure"):
        async with get_db() as db:
            conn_holder.append(db)
            assert db in _active_connections
            raise ValueError("simulated failure")

    assert len(conn_holder) == 1
    conn = conn_holder[0]
    assert conn not in _active_connections
    if hasattr(conn, "_thread") and conn._thread:
        assert not conn._thread.is_alive()


@pytest.mark.asyncio
async def test_close_db_connections_cleans_lingering_connections():
    """Verify close_db_connections cleans up any active connections."""
    conn = await aiosqlite.connect(settings.DB_PATH)
    _active_connections.add(conn)
    assert conn in _active_connections

    await close_db_connections()

    assert len(_active_connections) == 0
    if hasattr(conn, "_thread") and conn._thread:
        assert not conn._thread.is_alive()


@pytest.mark.asyncio
async def test_checkpoint_wal_executes_successfully():
    """Verify checkpoint_wal executes passive WAL checkpoint without error."""
    await init_db()
    # Insert some data so WAL has pages
    async with get_db() as db:
        await db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('test_key', 'val')")
        await db.commit()

    # Must complete cleanly
    await checkpoint_wal()


@pytest.mark.asyncio
async def test_lifespan_shutdown_runs_checkpoint_and_cleanup(monkeypatch):
    """Verify lifespan context manager executes checkpoint_wal and close_db_connections on shutdown."""
    called = {"checkpoint": False, "cleanup": False}

    async def fake_checkpoint():
        called["checkpoint"] = True

    async def fake_close():
        called["cleanup"] = True

    monkeypatch.setattr("backend.main.checkpoint_wal", fake_checkpoint)
    monkeypatch.setattr("backend.main.close_db_connections", fake_close)

    async with lifespan(app):
        pass

    assert called["checkpoint"] is True, "checkpoint_wal was not executed during lifespan shutdown!"
    assert called["cleanup"] is True, "close_db_connections was not executed during lifespan shutdown!"


def test_event_loop_close_without_runtime_error(tmp_path):
    """Verify event loop termination after close_db_connections does not trigger RuntimeError."""
    db_file = tmp_path / "loop_test.db"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_scenario():
        # Open connection and leave it in _active_connections
        conn = await aiosqlite.connect(db_file)
        _active_connections.add(conn)
        # Call cleanup
        await close_db_connections()

    try:
        loop.run_until_complete(run_scenario())
    finally:
        # Closing loop must not trigger any background thread exceptions
        loop.close()


@pytest.mark.asyncio
async def test_pragmas_foreign_keys_and_wal_enforced():
    """Verify that foreign keys, WAL mode, and busy timeout (5000ms) are strictly enforced."""
    import sqlite3

    async with get_db() as db:
        # Check foreign_keys pragma
        cur = await db.execute("PRAGMA foreign_keys;")
        row = await cur.fetchone()
        assert row[0] == 1, f"Expected PRAGMA foreign_keys=1, got {row[0]}"

        # Check journal_mode pragma
        cur = await db.execute("PRAGMA journal_mode;")
        row = await cur.fetchone()
        assert str(row[0]).lower() == "wal", f"Expected PRAGMA journal_mode=wal, got {row[0]}"

        # Check busy_timeout pragma
        cur = await db.execute("PRAGMA busy_timeout;")
        row = await cur.fetchone()
        assert row[0] == 5000, f"Expected PRAGMA busy_timeout=5000, got {row[0]}"

        # Verify foreign key constraint enforcement
        # Inserting a part for a non-existent file_id must raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute("""
                INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256)
                VALUES ('part_invalid', 'nonexistent_file_id_xyz', 0, 0, 100, 'sha256_mock')
            """)
            await db.commit()


@pytest.mark.asyncio
async def test_20_concurrent_connections_stress():
    """
    Adversarial Stress Test:
    Verify 20 concurrent connections opening, executing queries/transactions,
    and closing simultaneously under async tasks without deadlocks or database locks.
    """
    await init_db()
    total_concurrency = 20
    barrier = asyncio.Barrier(total_concurrency)

    async def concurrent_worker(worker_id: int):
        await barrier.wait()  # Synchronize start to maximize concurrency contention
        file_id = f"file_stress_{worker_id}"
        async with get_db() as db:
            # Transaction: Insert file and file_parts
            await db.execute(
                """
                INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256)
                VALUES (?, ?, 'application/octet-stream', 'other', 1024, ?)
                """,
                (file_id, f"stress_{worker_id}.dat", f"sha256_{worker_id}"),
            )
            await db.execute(
                """
                INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256)
                VALUES (?, ?, 0, 0, 1024, ?)
                """,
                (f"part_stress_{worker_id}", file_id, f"sha256_{worker_id}"),
            )
            await db.commit()

            # Read back
            cur = await db.execute("SELECT COUNT(*) FROM file_parts WHERE file_id = ?", (file_id,))
            row = await cur.fetchone()
            assert row[0] == 1

        return worker_id

    # Run all 20 concurrent tasks
    results = await asyncio.gather(*(concurrent_worker(i) for i in range(total_concurrency)))
    assert len(results) == total_concurrency

    # Verify global counts in DB
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM files WHERE id LIKE 'file_stress_%'")
        count = (await cur.fetchone())[0]
        assert count == total_concurrency, f"Expected {total_concurrency} files, found {count}"

    # Verify no leaked connections in active registry
    assert len(_active_connections) == 0, f"Leaked active connections: {_active_connections}"


@pytest.mark.asyncio
async def test_cancelling_task_mid_query_closes_cleanly():
    """
    Adversarial Stress Test:
    Cancelling an async task while a heavy query is actively executing in SQLite
    must cleanly close the SQLite connection, join worker threads, and leave no database locks.
    """
    await init_db()
    conn_holder = []

    async def heavy_query_worker():
        async with get_db() as db:
            conn_holder.append(db)
            # Run an intensive recursive CTE query that runs for a non-trivial duration
            await db.execute("""
                WITH RECURSIVE cnt(x) AS (
                    SELECT 1
                    UNION ALL
                    SELECT x+1 FROM cnt WHERE x < 2000000
                )
                SELECT count(*), max(x), min(x) FROM cnt;
            """)

    task = asyncio.create_task(heavy_query_worker())
    # Yield control briefly so worker enters get_db() and starts executing query
    await asyncio.sleep(0.01)
    assert len(conn_holder) == 1
    conn = conn_holder[0]

    # Cancel task mid-query
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Assert connection closed and thread terminated
    assert conn not in _active_connections
    if hasattr(conn, "_thread") and conn._thread:
        assert not conn._thread.is_alive(), "Worker thread must be terminated after mid-query cancellation!"

    # Verify database is not locked and subsequent operations work immediately
    async with get_db() as db:
        cur = await db.execute("SELECT 1")
        assert (await cur.fetchone())[0] == 1
        await db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('mid_cancel_check', 'ok')")
        await db.commit()


@pytest.mark.asyncio
async def test_wal_checkpointing_under_concurrent_load():
    """
    Adversarial Stress Test:
    WAL checkpointing executes without database locking even while concurrent
    readers and writers are actively querying and inserting into the database.
    """
    await init_db()
    stop_event = asyncio.Event()
    writers_done = []

    async def continuous_writer(worker_id: int):
        writes = 0
        while not stop_event.is_set() and writes < 30:
            async with get_db() as db:
                await db.execute(
                    "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)",
                    (f"key_{worker_id}_{writes}", f"val_{writes}"),
                )
                await db.commit()
            writes += 1
            await asyncio.sleep(0.005)
        writers_done.append(worker_id)

    async def continuous_reader():
        reads = 0
        while not stop_event.is_set() and reads < 30:
            async with get_db() as db:
                cur = await db.execute("SELECT count(*) FROM system_settings")
                await cur.fetchone()
            reads += 1
            await asyncio.sleep(0.005)

    # Spawn concurrent readers and writers
    tasks = [
        asyncio.create_task(continuous_writer(1)),
        asyncio.create_task(continuous_writer(2)),
        asyncio.create_task(continuous_reader()),
        asyncio.create_task(continuous_reader()),
    ]

    # Concurrently execute WAL checkpoint several times under load
    for _ in range(5):
        await asyncio.sleep(0.02)
        await checkpoint_wal()

    stop_event.set()
    await asyncio.gather(*tasks)

    # Final WAL checkpoint must execute without lock
    await checkpoint_wal()

    # Verify database is consistent and readable
    async with get_db() as db:
        cur = await db.execute("SELECT count(*) FROM system_settings")
        count = (await cur.fetchone())[0]
        assert count > 0, "Expected written records in system_settings"


