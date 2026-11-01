"""Regression tests for the four Critical app-breaking bugs.

C-1: album ZIP download crashed with `NameError: name 'io' is not defined`.
C-4: incomplete chunked uploads were streamed as if complete (truncated body
     with a lying Content-Length) because `upload_status` is only ever
     'uploading' or 'completed' on the chunked path, so the legacy
     'interrupted' guard never fired.
"""
import uuid
import zipfile
import io as _io

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.db.connection import get_db
from backend.api import routes


class MockStorageClient:
    """Minimal in-memory stand-in for the Telegram storage client."""

    def __init__(self, data_by_message_id):
        self.data_by_message_id = data_by_message_id

    async def stream_part_bytes(
        self,
        message_id,
        channel_id=None,
        file_id=None,
        access_hash=None,
        offset=0,
        limit=None,
        chunk_size=64 * 1024,
    ):
        data = self.data_by_message_id.get(message_id, b"")
        total_len = len(data)
        if offset >= total_len:
            return
        end = total_len if limit is None else min(total_len, offset + limit)
        curr = offset
        while curr < end:
            block_len = min(chunk_size, end - curr)
            yield data[curr:curr + block_len]
            curr += block_len


async def _insert_file(db, file_id, filename, size_bytes, total_parts, upload_status, mime="text/plain"):
    await db.execute(
        """
        INSERT INTO files (
            id, filename, mime_type, file_category, size_bytes, sha256,
            is_split, total_parts, upload_status, encryption, kind
        ) VALUES (?, ?, ?, 'document', ?, ?, ?, ?, ?, 'none', 'original')
        """,
        (file_id, filename, mime, size_bytes, "0" * 64, 1 if total_parts > 1 else 0,
         total_parts, upload_status),
    )


@pytest.mark.asyncio
async def test_c1_album_zip_download_returns_a_valid_archive(monkeypatch):
    """C-1: `io.BytesIO` was used without `import io` -> NameError on every call."""
    album_id = f"alb_{uuid.uuid4().hex[:8]}"
    file_id = f"v_{uuid.uuid4().hex}"
    payload = b"hello album zip\n" * 10

    async with get_db() as db:
        await db.execute("INSERT INTO albums (id, name) VALUES (?, ?)", (album_id, "Test Folder"))
        await _insert_file(db, file_id, "note.txt", len(payload), 1, "completed")
        await db.execute(
            """
            INSERT INTO file_parts (
                id, file_id, part_index, byte_offset, part_size_bytes,
                part_sha256, telegram_message_id, telegram_channel_id
            ) VALUES (?, ?, 0, 0, ?, ?, 4242, '-1001')
            """,
            (f"vp_{uuid.uuid4().hex}", file_id, len(payload), "0" * 64),
        )
        await db.execute(
            "INSERT INTO file_albums (file_id, album_id) VALUES (?, ?)",
            (file_id, album_id),
        )
        await db.commit()

    monkeypatch.setattr(
        routes.storage_client, "stream_part_bytes",
        MockStorageClient({4242: payload}).stream_part_bytes,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        res = await client.get(f"/api/v1/albums/{album_id}/download-zip")

    assert res.status_code == 200, f"album zip download failed: {res.status_code} {res.text[:300]}"
    assert res.headers["content-type"] == "application/zip"

    # The payload must be a real, readable zip archive containing the file.
    with zipfile.ZipFile(_io.BytesIO(res.content)) as zf:
        assert zf.namelist() == ["note.txt"]
        assert zf.read("note.txt") == payload


@pytest.mark.asyncio
async def test_c4_incomplete_upload_download_returns_409(monkeypatch):
    """C-4: a half-uploaded file must not be served with a full Content-Length."""
    file_id = f"v_{uuid.uuid4().hex}"
    total = 1000

    async with get_db() as db:
        # 2 parts expected, only 1 present, upload never completed.
        await _insert_file(db, file_id, "partial.bin", total, 2, "uploading")
        await db.execute(
            """
            INSERT INTO file_parts (
                id, file_id, part_index, byte_offset, part_size_bytes,
                part_sha256, telegram_message_id, telegram_channel_id
            ) VALUES (?, ?, 0, 0, ?, ?, 777, '-1001')
            """,
            (f"vp_{uuid.uuid4().hex}", file_id, 500, "0" * 64),
        )
        await db.commit()

    monkeypatch.setattr(
        routes.storage_client, "stream_part_bytes",
        MockStorageClient({777: b"x" * 500}).stream_part_bytes,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        download = await client.get(f"/api/v1/files/{file_id}/download")
        view = await client.get(f"/api/v1/files/{file_id}/view")
        stream = await client.get(f"/api/v1/virtual-stream/{file_id}")

    assert download.status_code == 409, f"expected 409, got {download.status_code}"
    assert view.status_code == 409, f"expected 409, got {view.status_code}"
    assert stream.status_code == 409, f"expected 409, got {stream.status_code}"
    assert "interrupted" in download.json()["detail"].lower()


@pytest.mark.asyncio
async def test_c4_complete_upload_still_streams(monkeypatch):
    """C-4 must not break the happy path: a complete file still streams."""
    file_id = f"v_{uuid.uuid4().hex}"
    payload = b"complete payload" * 10

    async with get_db() as db:
        await _insert_file(db, file_id, "whole.bin", len(payload), 1, "completed")
        await db.execute(
            """
            INSERT INTO file_parts (
                id, file_id, part_index, byte_offset, part_size_bytes,
                part_sha256, telegram_message_id, telegram_channel_id
            ) VALUES (?, ?, 0, 0, ?, ?, 888, '-1001')
            """,
            (f"vp_{uuid.uuid4().hex}", file_id, len(payload), "0" * 64),
        )
        await db.commit()

    monkeypatch.setattr(
        routes.storage_client, "stream_part_bytes",
        MockStorageClient({888: payload}).stream_part_bytes,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        res = await client.get(f"/api/v1/files/{file_id}/download")

    assert res.status_code == 200
    assert res.content == payload


@pytest.mark.asyncio
async def test_c4_is_upload_incomplete_matches_list_endpoint():
    """C-4: the streaming guard and the gallery's `is_interrupted` must agree."""
    from backend.api.routes import is_upload_incomplete

    # Never finished: short part set while status is still 'uploading'.
    assert is_upload_incomplete(
        {"upload_status": "uploading", "total_parts": 3},
        [{"part_index": 0}],
    ) is True

    # Finished.
    assert is_upload_incomplete(
        {"upload_status": "completed", "total_parts": 3},
        [{"part_index": 0}, {"part_index": 1}, {"part_index": 2}],
    ) is False

    # Explicitly interrupted (catalog recovery path).
    assert is_upload_incomplete(
        {"upload_status": "interrupted", "total_parts": 1},
        [{"part_index": 0}],
    ) is True
