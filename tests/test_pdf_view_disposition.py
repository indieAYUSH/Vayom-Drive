import uuid
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.db.connection import get_db, init_db
from backend.storage.client import storage_client


async def _init_test_env():
    await init_db()
    settings.ensure_directories()
    await storage_client.disconnect()


def test_pdf_view_vs_download_disposition():
    async def _run():
        await _init_test_env()
        client = TestClient(app)
        file_id = f"test-pdf-{uuid.uuid4().hex[:8]}"

        # Seed mock blob
        mock_dir = settings.DATA_DIR / "mock_blobs"
        mock_dir.mkdir(parents=True, exist_ok=True)
        pdf_bytes = b"%PDF-1.4 Mock PDF Content For Testing"
        (mock_dir / "998877.bin").write_bytes(pdf_bytes)

        try:
            async with get_db() as db:
                await db.execute(
                    """
                    INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, has_thumbnail, is_deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (file_id, "document.pdf", "application/pdf", "document", len(pdf_bytes), "mocksha", 0, 1, 0)
                )
                await db.execute(
                    """
                    INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
                    VALUES (?, ?, 0, 0, ?, 'mocksha', 998877)
                    """,
                    (f"part-{uuid.uuid4().hex[:8]}", file_id, len(pdf_bytes))
                )
                await db.commit()

            # 1. /view endpoint must return inline disposition
            view_res = client.get(f"/api/v1/files/{file_id}/view")
            assert view_res.status_code == 200
            assert "inline" in view_res.headers.get("content-disposition", "")
            assert view_res.headers.get("content-type") == "application/pdf"

            # 2. /download endpoint must return attachment disposition
            dl_res = client.get(f"/api/v1/files/{file_id}/download")
            assert dl_res.status_code == 200
            assert "attachment" in dl_res.headers.get("content-disposition", "")
            assert dl_res.headers.get("content-type") == "application/pdf"

            # 3. /download?inline=true must return inline disposition
            dl_inline_res = client.get(f"/api/v1/files/{file_id}/download?inline=true")
            assert dl_inline_res.status_code == 200
            assert "inline" in dl_inline_res.headers.get("content-disposition", "")

            # 4. /files listing must generate preview_url pointing to /view for documents/PDFs
            files_res = client.get("/api/v1/files?q=document.pdf")
            assert files_res.status_code == 200
            items = files_res.json()
            if isinstance(items, dict):
                items = items.get("items", [])
            target = next((item for item in items if item["id"] == file_id), None)
            assert target is not None
            assert target["preview_url"] == f"/api/v1/files/{file_id}/view"

        finally:
            async with get_db() as db:
                await db.execute("DELETE FROM file_parts WHERE file_id = ?", (file_id,))
                await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
                await db.commit()
            blob_file = mock_dir / "998877.bin"
            if blob_file.exists():
                blob_file.unlink()

    import asyncio
    asyncio.run(_run())
