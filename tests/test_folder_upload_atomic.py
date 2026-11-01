import asyncio
import os
import io
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.db.connection import get_db, init_db
from backend.config import settings

@pytest.fixture(autouse=True)
def setup_test_db():
    async def _init():
        await init_db()
    asyncio.run(_init())

def test_album_creation_idempotency():
    """Verify POST /api/v1/albums returns existing album when same name is requested."""
    client = TestClient(app)
    album_name = f"Vacation_{uuid.uuid4().hex[:6]}"

    # First creation
    res1 = client.post("/api/v1/albums", json={"name": album_name, "description": "Summer trip"})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["name"] == album_name
    album_id1 = data1["id"]

    # Second creation with same name (case-insensitive)
    res2 = client.post("/api/v1/albums", json={"name": album_name.lower(), "description": "Another call"})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["id"] == album_id1

    # Empty name rejection
    res_empty = client.post("/api/v1/albums", json={"name": "   "})
    assert res_empty.status_code == 400

def test_upload_file_atomic_album_association():
    """Verify POST /api/v1/upload with album_id atomically links file to album in SQLite."""
    async def _run():
        client = TestClient(app)
        album_name = f"FolderDrop_{uuid.uuid4().hex[:6]}"
        res_a = client.post("/api/v1/albums", json={"name": album_name})
        assert res_a.status_code == 200
        album_id = res_a.json()["id"]

        # Upload a file with album_id in form data
        file_bytes = b"Sample photo data inside folder"
        res_up = client.post(
            "/api/v1/upload",
            files={"file": ("photo1.jpg", io.BytesIO(file_bytes), "image/jpeg")},
            data={"album_id": album_id}
        )
        assert res_up.status_code == 200
        file_id = res_up.json()["file_id"]

        # Verify atomic link in file_albums table
        async with get_db() as db:
            cur = await db.execute("SELECT album_id FROM file_albums WHERE file_id = ?", (file_id,))
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == album_id

            # Verify cover_file_id was set on album
            c_cur = await db.execute("SELECT cover_file_id FROM albums WHERE id = ?", (album_id,))
            c_row = await c_cur.fetchone()
            assert c_row[0] == file_id

        # Verify querying files by album_id returns the uploaded file
        res_list = client.get(f"/api/v1/files?album_id={album_id}")
        assert res_list.status_code == 200
        files = res_list.json()
        assert any(f["id"] == file_id for f in files)

    asyncio.run(_run())

def test_chunked_upload_init_album_association():
    """Verify POST /api/v1/upload/init with album_id links file to album."""
    async def _run():
        client = TestClient(app)
        album_name = f"LargeFolder_{uuid.uuid4().hex[:6]}"
        res_a = client.post("/api/v1/albums", json={"name": album_name})
        assert res_a.status_code == 200
        album_id = res_a.json()["id"]

        # Initialize chunked upload with album_id
        res_init = client.post(
            "/api/v1/upload/init",
            data={
                "filename": "video_chunked.mp4",
                "total_size": "2000000000",
                "mime_type": "video/mp4",
                "total_parts": "2",
                "album_id": album_id
            }
        )
        assert res_init.status_code == 200
        file_id = res_init.json()["file_id"]

        # Verify link in file_albums
        async with get_db() as db:
            cur = await db.execute("SELECT album_id FROM file_albums WHERE file_id = ?", (file_id,))
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == album_id

    asyncio.run(_run())
