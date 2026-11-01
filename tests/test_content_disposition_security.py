"""
Regression tests for B-7: Unsanitized filename in `Content-Disposition`.

Verifies the shared RFC 6266 / RFC 5987 helper (`format_content_disposition`)
and that every download path emitting `Content-Disposition` is protected
against CRLF header injection (CWE-113) while still round-tripping non-ASCII
filenames via the `filename*=UTF-8''...` parameter.
"""
import asyncio
import urllib.parse
import uuid

from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.api.routes import format_content_disposition
from backend.db.connection import get_db, init_db

HOSTILE_FILENAME = 'evil"\r\nX-Injected: 1.mp4'
NON_ASCII_FILENAME = "Résumé ☕ – final.mp4"


async def _init_test_env():
    await init_db()
    settings.ensure_directories()


def _extract_filename_star(header: str) -> str:
    """Decodes the RFC 5987 `filename*=UTF-8''...` parameter back to Unicode."""
    marker = "filename*=UTF-8''"
    assert marker in header, f"Missing RFC 5987 parameter in {header!r}"
    return urllib.parse.unquote(header.split(marker, 1)[1])


def test_format_content_disposition_strips_header_breakers():
    header = format_content_disposition(HOSTILE_FILENAME)

    # No CR/LF can survive into the header value
    assert "\r" not in header and "\n" not in header
    # The only double quotes are the ASCII fallback delimiters
    assert header.count('"') == 2
    assert 'filename="evilX-Injected: 1.mp4"' in header

    # The full original name is preserved losslessly via percent-encoding
    assert _extract_filename_star(header) == HOSTILE_FILENAME


def test_format_content_disposition_preserves_non_ascii_names():
    header = format_content_disposition(NON_ASCII_FILENAME, disposition_type="attachment")

    assert header.startswith("attachment; ")
    assert header.isascii()
    assert _extract_filename_star(header) == NON_ASCII_FILENAME


def test_format_content_disposition_fallback_for_empty_names():
    for value in ("", None, '"""', "\r\n"):
        header = format_content_disposition(value)
        assert header.startswith("inline; ")
        assert 'filename="file"' in header
        assert "\r" not in header and "\n" not in header


def test_format_content_disposition_defaults_to_inline():
    assert format_content_disposition("clip.mp4").startswith("inline; ")
    assert format_content_disposition("clip.mp4", disposition_type="attachment").startswith("attachment; ")


def test_download_and_virtual_stream_headers_are_safe():
    async def _run():
        await _init_test_env()
        client = TestClient(app)
        file_id = f"b7-{uuid.uuid4().hex[:8]}"

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, is_deleted)
                VALUES (?, ?, ?, ?, ?, ?, 0, 1, 0)
                """,
                (file_id, HOSTILE_FILENAME, "video/mp4", "video", 100, "mock_sha")
            )
            await db.execute(
                """
                INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id)
                VALUES (?, ?, 0, 0, 100, 'mock_sha', 424242)
                """,
                (f"part-{uuid.uuid4().hex[:8]}", file_id)
            )
            await db.commit()

        try:
            urls = (
                f"/api/v1/files/{file_id}/download",
                f"/api/v1/virtual-stream/{file_id}",
            )
            for url in urls:
                res = client.head(url)
                assert res.status_code == 200, f"{url} returned {res.status_code}"
                header = res.headers["content-disposition"]
                assert "\r" not in header and "\n" not in header, f"CRLF leaked into {url} header"
                assert header.count('"') == 2, f"Unescaped quote leaked into {url} header"
                assert _extract_filename_star(header) == HOSTILE_FILENAME
        finally:
            async with get_db() as db:
                await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
                await db.commit()

    asyncio.run(_run())


def test_catalog_export_uses_safe_attachment_header():
    async def _run():
        await _init_test_env()
        client = TestClient(app)

        res = client.get("/api/v1/vault/catalog/export")
        assert res.status_code == 200

        header = res.headers["content-disposition"]
        assert header.startswith('attachment; filename="vayom_vault_catalog_')
        assert "filename*=UTF-8''vayom_vault_catalog_" in header
        assert "\r" not in header and "\n" not in header

    asyncio.run(_run())
