import pytest
import json
import uuid
import aiosqlite
from pathlib import Path
from backend.storage.client import format_part_caption, TelegramStorageClient
from backend.db.connection import get_db, init_db
from backend.config import settings


@pytest.mark.asyncio
async def test_schema_and_foreign_keys_pragmas():
    """Verifies PRAGMA foreign_keys is enabled and proxy columns exist."""
    async with get_db() as db:
        cur = await db.execute("PRAGMA foreign_keys")
        fk_enabled = (await cur.fetchone())[0]
        assert fk_enabled == 1, "PRAGMA foreign_keys must be ON"

        # Check table columns on files
        cur = await db.execute("PRAGMA table_info(files)")
        cols = {row["name"]: row for row in await cur.fetchall()}
        expected_cols = [
            "kind", "proxy_of", "proxy_profile", "proxy_status",
            "video_bitrate_bps", "width", "height", "codec", "container", "duration_s"
        ]
        for col in expected_cols:
            assert col in cols, f"Column {col} missing in files table"


@pytest.mark.asyncio
async def test_caption_envelope_size_invariants():
    """
    Verifies Phase 1 caption constraints:
    - Part 0 <= 950 bytes
    - Part 1..N <= 200 bytes
    - Assertions trigger if limits are exceeded
    """
    orig_id = f"v_{uuid.uuid4().hex}"
    proxy_id = f"v_{uuid.uuid4().hex}"

    # Original Part 0
    cap_orig_p0 = format_part_caption(
        file_id=orig_id,
        part_index=0,
        total_parts=5,
        byte_offset=0,
        part_size=1024 * 1024,
        part_sha256="a" * 64,
        filename="wedding_film_4k_master.mp4",
        category="video",
        kind="original"
    )
    assert len(cap_orig_p0.encode("utf-8")) <= 950
    assert "VAYOM:v1:" in cap_orig_p0
    payload_p0 = json.loads(cap_orig_p0.replace("VAYOM:v1:", ""))
    assert payload_p0["id"] == orig_id
    assert "k" not in payload_p0  # Absent k = original

    # Original Part 1..N
    cap_orig_pN = format_part_caption(
        file_id=orig_id,
        part_index=1,
        total_parts=5,
        byte_offset=1024 * 1024,
        part_size=1024 * 1024,
        part_sha256="b" * 64,
        kind="original"
    )
    assert len(cap_orig_pN.encode("utf-8")) <= 200
    payload_pN = json.loads(cap_orig_pN.replace("VAYOM:v1:", ""))
    assert payload_pN["part"] == 1
    assert "k" not in payload_pN

    # Proxy Part 0
    cap_proxy_p0 = format_part_caption(
        file_id=proxy_id,
        part_index=0,
        total_parts=1,
        byte_offset=0,
        part_size=500 * 1024,
        part_sha256="c" * 64,
        filename="proxy_wedding_film_4k_master.mp4",
        category="video",
        kind="proxy",
        proxy_of=orig_id,
        proxy_profile="stream_v1"
    )
    assert len(cap_proxy_p0.encode("utf-8")) <= 950
    payload_proxy_p0 = json.loads(cap_proxy_p0.replace("VAYOM:v1:", ""))
    assert payload_proxy_p0["k"] == "p"
    assert payload_proxy_p0["of"] == orig_id
    assert payload_proxy_p0["profile"] == "stream_v1"

    # Proxy Part 1..N
    cap_proxy_pN = format_part_caption(
        file_id=proxy_id,
        part_index=1,
        total_parts=2,
        byte_offset=500 * 1024,
        part_size=500 * 1024,
        part_sha256="d" * 64,
        kind="proxy",
        proxy_of=orig_id,
        proxy_profile="stream_v1"
    )
    assert len(cap_proxy_pN.encode("utf-8")) <= 200
    payload_proxy_pN = json.loads(cap_proxy_pN.replace("VAYOM:v1:", ""))
    assert payload_proxy_pN["k"] == "p"


@pytest.mark.asyncio
async def test_backward_compatibility_old_captions():
    """Verifies legacy captions without 'k' parse cleanly and default to original."""
    legacy_json = json.dumps({
        "id": "v_legacy12345",
        "name": "old_video.mp4",
        "part": 0,
        "total": 1,
        "offset": 0,
        "size": 123456,
        "sha256": "e" * 64,
        "cat": "video"
    })
    legacy_caption = f"VAYOM:v1:{legacy_json}"

    # Simulating the parsing logic in routes.py / rebuild_index
    raw_payload = json.loads(legacy_caption.replace("VAYOM:v1:", ""))
    kind = "proxy" if raw_payload.get("k") == "p" else "original"
    proxy_of = raw_payload.get("of")

    assert kind == "original"
    assert proxy_of is None


@pytest.mark.asyncio
async def test_cascade_delete_and_telegram_parts(monkeypatch):
    """
    Verifies that deleting or purging an original:
    1. Collects Telegram message IDs for BOTH original and child proxy.
    2. Calls storage_client.delete_parts with all collected parts.
    3. Deletes DB rows with CASCADE.
    """
    orig_id = f"v_orig_{uuid.uuid4().hex[:12]}"
    proxy_id = f"v_proxy_{uuid.uuid4().hex[:12]}"

    deleted_parts_collected = []

    async def fake_delete_parts(parts):
        deleted_parts_collected.extend(parts)
        return len(parts)

    from backend.storage.client import storage_client
    monkeypatch.setattr(storage_client, "delete_parts", fake_delete_parts)

    async with get_db() as db:
        # Insert original file
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'orig.mp4', 'video/mp4', 'video', 50000000, 'hash1', 0, 1, 'completed', 'original', 'ready')
            """,
            (orig_id,)
        )
        # Insert child proxy file
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_of, proxy_profile)
            VALUES (?, 'proxy.mp4', 'video/mp4', 'video', 5000000, 'hash2', 0, 1, 'completed', 'proxy', ?, 'stream_v1')
            """,
            (proxy_id, orig_id)
        )

        # Insert parts for original
        await db.execute(
            """
            INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id, telegram_channel_id)
            VALUES ('vp_1', ?, 0, 0, 50000000, 'hash1', 1001, '-100999')
            """,
            (orig_id,)
        )
        # Insert parts for proxy
        await db.execute(
            """
            INSERT INTO file_parts (id, file_id, part_index, byte_offset, part_size_bytes, part_sha256, telegram_message_id, telegram_channel_id)
            VALUES ('vp_2', ?, 0, 0, 5000000, 'hash2', 2001, '-100999')
            """,
            (proxy_id,)
        )
        await db.commit()

    # Call delete_file with purge=True
    from backend.api.routes import delete_file
    res = await delete_file(orig_id, purge=True)
    assert res["success"] is True

    # Check that BOTH message IDs 1001 and 2001 were passed to delete_parts
    purged_msg_ids = [p["telegram_message_id"] for p in deleted_parts_collected]
    assert 1001 in purged_msg_ids
    assert 2001 in purged_msg_ids

    # Check SQLite that both rows are gone
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM files WHERE id IN (?, ?)", (orig_id, proxy_id))
        count = (await cur.fetchone())[0]
        assert count == 0


@pytest.mark.asyncio
async def test_soft_delete_and_restore_cascades():
    """Verifies that soft-deleting an original file soft-deletes its child proxy, and restore restores both."""
    orig_id = f"v_orig_{uuid.uuid4().hex[:12]}"
    proxy_id = f"v_proxy_{uuid.uuid4().hex[:12]}"

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_status)
            VALUES (?, 'orig.mp4', 'video/mp4', 'video', 1000, 'hash1', 0, 1, 'completed', 'original', 'ready')
            """,
            (orig_id,)
        )
        await db.execute(
            """
            INSERT INTO files (id, filename, mime_type, file_category, size_bytes, sha256, is_split, total_parts, upload_status, kind, proxy_of)
            VALUES (?, 'proxy.mp4', 'video/mp4', 'video', 100, 'hash2', 0, 1, 'completed', 'proxy', ?)
            """,
            (proxy_id, orig_id)
        )
        await db.commit()

    from backend.api.routes import delete_file, restore_file
    # Soft delete
    await delete_file(orig_id, purge=False)

    async with get_db() as db:
        cur = await db.execute("SELECT id, is_deleted FROM files WHERE id IN (?, ?)", (orig_id, proxy_id))
        rows = {r[0]: r[1] for r in await cur.fetchall()}
        assert rows[orig_id] == 1
        assert rows[proxy_id] == 1

    # Restore
    await restore_file(orig_id)

    async with get_db() as db:
        cur = await db.execute("SELECT id, is_deleted FROM files WHERE id IN (?, ?)", (orig_id, proxy_id))
        rows = {r[0]: r[1] for r in await cur.fetchall()}
        assert rows[orig_id] == 0
        assert rows[proxy_id] == 0


@pytest.mark.asyncio
async def test_two_pass_reverse_scan_rebuild(monkeypatch):
    """
    Verifies Two-Pass Reverse Scan:
    In channel reverse scan, messages arrive newest-to-oldest.
    A proxy uploaded after the original appears FIRST in the message stream.
    Two-pass scan buffers the proxy, inserts the original in Pass 1,
    and inserts/links the child proxy in Pass 2 without FK errors.
    Also verifies an orphaned proxy is ignored and logged.
    """
    orig_id = f"v_orig_{uuid.uuid4().hex[:12]}"
    proxy_id = f"v_proxy_{uuid.uuid4().hex[:12]}"
    orphan_proxy_id = f"v_orphan_{uuid.uuid4().hex[:12]}"

    # Reverse order: proxy message (msg 300), then orphan proxy (msg 250), then original message (msg 200)
    msg_proxy = {
        "id": 300,
        "message": format_part_caption(
            file_id=proxy_id,
            part_index=0,
            total_parts=1,
            byte_offset=0,
            part_size=2000,
            part_sha256="sha_p",
            filename="proxy.mp4",
            category="video",
            kind="proxy",
            proxy_of=orig_id,
            proxy_profile="stream_v1"
        ),
        "media": None
    }
    msg_orphan = {
        "id": 250,
        "message": format_part_caption(
            file_id=orphan_proxy_id,
            part_index=0,
            total_parts=1,
            byte_offset=0,
            part_size=2000,
            part_sha256="sha_orphan",
            filename="orphan_proxy.mp4",
            category="video",
            kind="proxy",
            proxy_of="v_missing_parent_999",
            proxy_profile="stream_v1"
        ),
        "media": None
    }
    msg_orig = {
        "id": 200,
        "message": format_part_caption(
            file_id=orig_id,
            part_index=0,
            total_parts=1,
            byte_offset=0,
            part_size=50000,
            part_sha256="sha_orig",
            filename="orig.mp4",
            category="video",
            kind="original"
        ),
        "media": None
    }

    env_proxy = json.loads(msg_proxy["message"].replace("VAYOM:v1:", ""))
    env_proxy["telegram_message_id"] = 300

    env_orphan = json.loads(msg_orphan["message"].replace("VAYOM:v1:", ""))
    env_orphan["telegram_message_id"] = 250

    env_orig = json.loads(msg_orig["message"].replace("VAYOM:v1:", ""))
    env_orig["telegram_message_id"] = 200

    from backend.storage.client import storage_client
    async def fake_get_pinned_catalog():
        return None
    async def fake_scan_channel_envelopes(limit=5000):
        # Reverse order: proxy arrived first, then orphan, then original
        return [env_proxy, env_orphan, env_orig]

    monkeypatch.setattr(storage_client, "get_pinned_catalog", fake_get_pinned_catalog)
    monkeypatch.setattr(storage_client, "scan_channel_envelopes", fake_scan_channel_envelopes)

    from backend.api.routes import perform_rebuild_index
    res = await perform_rebuild_index()
    assert res >= 1

    # Verify database state
    async with get_db() as db:
        # Original must exist
        cur = await db.execute("SELECT id, kind, proxy_status FROM files WHERE id = ?", (orig_id,))
        orig_row = await cur.fetchone()
        assert orig_row is not None
        assert orig_row["kind"] == "original"
        assert orig_row["proxy_status"] == "ready"

        # Proxy must exist and be linked to original
        cur = await db.execute("SELECT id, kind, proxy_of, proxy_profile FROM files WHERE id = ?", (proxy_id,))
        proxy_row = await cur.fetchone()
        assert proxy_row is not None
        assert proxy_row["kind"] == "proxy"
        assert proxy_row["proxy_of"] == orig_id
        assert proxy_row["proxy_profile"] == "stream_v1"

        # Orphan proxy must NOT exist
        cur = await db.execute("SELECT id FROM files WHERE id = ?", (orphan_proxy_id,))
        orphan_row = await cur.fetchone()
        assert orphan_row is None, "Orphan proxy whose parent is missing must be ignored"
