"""Regression tests for bug-swarm fixes.

B1: the 60s upload-tracker TTL pop must only remove the tracker entry created
by the same upload attempt. The frontend retries a failed upload with the SAME
task_id, so an unconditional pop evicts the live retried entry mid-upload
(KeyError -> failure handler purges uploaded Telegram parts -> 500).
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.routes import upload_progress_tracker, expire_tracker_entry
from backend.storage.client import storage_client

# Attempt 1 must surface as a plain 500, not raise through the test client.
client = TestClient(app, raise_server_exceptions=False)


def _post_upload(task_id: str) -> object:
    payload = b"b1 regression payload " * 512
    return client.post(
        f"/api/v1/upload?task_id={task_id}",
        files={"file": ("b1_retry.bin", payload, "application/octet-stream")},
    )


def test_b1_ttl_pop_spares_retried_entry_same_task_id(monkeypatch):
    task_id = "b1_task_retry"
    upload_progress_tracker.pop(task_id, None)

    calls = {"n": 0}
    deleted: list = []
    real_upload_part = storage_client.upload_part

    async def flaky_upload_part(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated network error")
        return await real_upload_part(*args, **kwargs)

    async def record_delete(parts):
        deleted.extend(parts)
        return len(parts)

    monkeypatch.setattr(storage_client, "upload_part", flaky_upload_part)
    monkeypatch.setattr(storage_client, "delete_parts", record_delete)

    # Attempt 1 fails -> failed marker armed, tagged with its attempt's file_id.
    res1 = _post_upload(task_id)
    assert res1.status_code == 500
    failed_entry = upload_progress_tracker.get(task_id)
    assert failed_entry is not None
    assert failed_entry["status"] == "failed"
    owner_file_id = failed_entry.get("file_id")
    assert owner_file_id, "failed marker must carry the owning attempt's file_id"

    # Attempt 2 (same task_id, exactly what the UI retry sends) succeeds.
    res2 = _post_upload(task_id)
    assert res2.status_code == 200
    live = upload_progress_tracker[task_id]
    assert live["status"] == "done"
    assert live["file_id"] != owner_file_id

    # Attempt 1's 60s TTL fires while attempt 2's entry is live.
    asyncio.run(expire_tracker_entry(task_id, owner_file_id, delay=0))
    assert task_id in upload_progress_tracker, "stale TTL must not evict the live retried entry"
    assert upload_progress_tracker[task_id]["status"] == "done"
    assert deleted == [], "successful retry must not have its Telegram parts purged"

    # The live entry's own TTL (matching owner) does expire it.
    asyncio.run(expire_tracker_entry(task_id, live["file_id"], delay=0))
    assert task_id not in upload_progress_tracker


def test_b1_flood_wait_ttl_snapshots_existing_owner(monkeypatch):
    """The flood-wait early-expiry site must snapshot the entry's current
    owner_file_id instead of popping unconditionally."""
    from backend.api import routes

    task_id = "b1_task_floodwait"
    upload_progress_tracker.pop(task_id, None)

    # Simulate a prior attempt's live entry (as created at upload start).
    upload_progress_tracker[task_id] = {
        "task_id": task_id,
        "file_id": "v_priorattempt0000000000000000000000ff",
        "status": "buffering",
        "progress_percent": 5,
    }
    # Force the flood-wait branch (property on the class).
    monkeypatch.setattr(
        type(storage_client), "flood_wait_seconds", property(lambda self: 120)
    )

    res = _post_upload(task_id)
    assert res.status_code == 429

    entry = upload_progress_tracker.get(task_id)
    assert entry is not None and entry["status"] == "failed"

    # A retry replaced the entry with a new attempt before the TTL fires.
    upload_progress_tracker[task_id] = {
        "task_id": task_id,
        "file_id": "v_retryattempt000000000000000000000ee",
        "status": "buffering",
        "progress_percent": 5,
    }
    prior_owner = "v_priorattempt0000000000000000000000ff"
    asyncio.run(expire_tracker_entry(task_id, prior_owner, delay=0))
    assert task_id in upload_progress_tracker
    assert upload_progress_tracker[task_id]["file_id"] == "v_retryattempt000000000000000000000ee"

    # With no retry, the matching owner is expired.
    asyncio.run(expire_tracker_entry(task_id, "v_retryattempt000000000000000000000ee", delay=0))
    assert task_id not in upload_progress_tracker


def test_b1_helper_unit_semantics():
    task_id = "b1_helper_unit"
    upload_progress_tracker.pop(task_id, None)

    # Missing entry: no-op, no error.
    asyncio.run(expire_tracker_entry(task_id, None, delay=0))
    assert task_id not in upload_progress_tracker

    # Ownerless entry (legacy failed marker without file_id) expires on a
    # None-owner schedule and is spared by an owner-bearing schedule.
    upload_progress_tracker[task_id] = {"task_id": task_id, "status": "failed"}
    asyncio.run(expire_tracker_entry(task_id, "v_someowner00000000000000000000000aa", delay=0))
    assert task_id in upload_progress_tracker
    asyncio.run(expire_tracker_entry(task_id, None, delay=0))
    assert task_id not in upload_progress_tracker


# ---------------------------------------------------------------------------
# B2: duplicate-hash proxy clones share Telegram messages; purging one file
# must not delete messages that the surviving file's proxy still references.
# ---------------------------------------------------------------------------

def _seed_b2_vault() -> None:
    from backend.db.connection import get_db, init_db

    async def seed():
        await init_db()
        async with get_db() as db:
            rows = [
                # (id, kind, proxy_of, sha256)
                ("v_aa000000000000000000000000000b2a1", "original", None, "sha_dup_1"),
                ("v_aa000000000000000000000000000b2b2", "original", None, "sha_dup_1"),
                ("v_aa000000000000000000000000000b2c3", "proxy", "v_aa000000000000000000000000000b2a1", "sha_prx"),
                ("v_aa000000000000000000000000000b2d4", "proxy", "v_aa000000000000000000000000000b2b2", "sha_prx"),
            ]
            for fid, kind, proxy_of, sha in rows:
                await db.execute(
                    """
                    INSERT INTO files (
                        id, filename, mime_type, file_category, size_bytes, sha256,
                        total_parts, upload_status, kind, proxy_of, proxy_profile,
                        proxy_status, storage_backend
                    ) VALUES (?, ?, 'video/mp4', 'video', ?, ?, 1, 'completed', ?, ?, ?, 'ready', 'telegram')
                    """,
                    (fid, f"{fid}.mp4", 1000, sha, kind, proxy_of, "stream_v2_480p"),
                )
            parts = [
                # (part_id, file_id, msg_id) — proxies share msg 7001 (clone)
                ("vp_b2_oa1", "v_aa000000000000000000000000000b2a1", 6001),
                ("vp_b2_ob2", "v_aa000000000000000000000000000b2b2", 6002),
                ("vp_b2_pa3", "v_aa000000000000000000000000000b2c3", 7001),
                ("vp_b2_pb4", "v_aa000000000000000000000000000b2d4", 7001),
            ]
            for pid, fid, msg in parts:
                await db.execute(
                    """
                    INSERT INTO file_parts (
                        id, file_id, part_index, byte_offset, part_size_bytes,
                        part_sha256, telegram_message_id, telegram_channel_id,
                        storage_backend, storage_uri
                    ) VALUES (?, ?, 0, 0, 1000, 'psha', ?, '-100123456789', 'telegram', ?)
                    """,
                    (pid, fid, msg, f"tg://-100123456789/{msg}"),
                )
            await db.commit()

    asyncio.run(seed())


def _remaining_file_ids() -> set:
    from backend.db.connection import get_db

    async def q():
        async with get_db() as db:
            cur = await db.execute("SELECT id FROM files")
            return {r[0] for r in await cur.fetchall()}

    return asyncio.run(q())


def test_b2_purge_keeps_shared_clone_messages(monkeypatch):
    _seed_b2_vault()
    deleted: list = []

    async def record_delete(parts):
        deleted.extend(p["telegram_message_id"] for p in parts)
        return len(parts)

    monkeypatch.setattr(storage_client, "delete_parts", record_delete)

    # Purge original A: its own msg 6001 + proxy P_A's shared msg 7001 are
    # candidates, but 7001 is still referenced by P_B and must survive.
    res = client.delete("/api/v1/files/v_aa000000000000000000000000000b2a1?purge=true")
    assert res.status_code == 200
    assert deleted == [6001], "shared clone message must not be purged while P_B references it"

    remaining = _remaining_file_ids()
    assert "v_aa000000000000000000000000000b2b2" in remaining
    assert "v_aa000000000000000000000000000b2d4" in remaining
    assert "v_aa000000000000000000000000000b2a1" not in remaining
    assert "v_aa000000000000000000000000000b2c3" not in remaining

    # Purge original B: now nothing else references 7001, so it is deleted.
    res_b = client.delete("/api/v1/files/v_aa000000000000000000000000000b2b2?purge=true")
    assert res_b.status_code == 200
    assert sorted(deleted) == [6001, 6002, 7001]


# ---------------------------------------------------------------------------
# B4: delta tail sync must read the proxy profile from the caption key the
# writer actually uses ("profile"), not "prof".
# ---------------------------------------------------------------------------

def test_b4_tail_sync_reads_profile_key():
    import json
    from backend.api.routes import perform_rebuild_index
    from backend.storage.client import format_part_caption
    from backend.transcoding.encoder import PROXY_PROFILE
    from backend.db.connection import get_db

    parent_id = "v_bb000000000000000000000000000b4p1"
    proxy_id = "v_bb000000000000000000000000000b4x2"

    caption = format_part_caption(
        proxy_id, 0, 1, 0, 1000, "psha1", filename="clip.mp4",
        category="video", kind="proxy", proxy_of=parent_id,
        proxy_profile=PROXY_PROFILE,
    )
    env = json.loads(caption.split("VAYOM:v1:", 1)[1])
    assert env["profile"] == PROXY_PROFILE, "caption writer must use the 'profile' key"

    async def fake_pinned_catalog():
        return {
            "max_message_id": 100,
            "files": [{
                "id": parent_id, "filename": "clip.mp4", "mime_type": "video/mp4",
                "file_category": "video", "size_bytes": 5000, "sha256": "osha1",
                "total_parts": 1, "parts": [{
                    "part_index": 0, "offset": 0, "size": 5000, "sha256": "psha0",
                    "telegram_message_id": 6001,
                }],
            }],
        }

    async def fake_scan(limit=1000, min_id=0):
        return [{
            "id": proxy_id, "k": "p", "of": parent_id, "part": 0, "total": 1,
            "offset": 0, "size": 1000, "sha256": "psha1", "name": "clip.mp4",
            "profile": PROXY_PROFILE, "telegram_message_id": 7001,
        }]

    orig_pinned = storage_client.get_pinned_catalog
    orig_scan = storage_client.scan_channel_envelopes
    storage_client.get_pinned_catalog = fake_pinned_catalog
    storage_client.scan_channel_envelopes = fake_scan
    try:
        count = asyncio.run(perform_rebuild_index())
        assert count >= 1

        async def q():
            async with get_db() as db:
                cur = await db.execute(
                    "SELECT proxy_profile FROM files WHERE id = ?", (proxy_id,)
                )
                row = await cur.fetchone()
                return row[0] if row else None

        assert asyncio.run(q()) == PROXY_PROFILE
    finally:
        storage_client.get_pinned_catalog = orig_pinned
        storage_client.scan_channel_envelopes = orig_scan


# ---------------------------------------------------------------------------
# B5: a failed /auth/qr/start must restore (reconnect) the storage client it
# disconnected, mirroring the /bot failure path.
# ---------------------------------------------------------------------------

def test_b5_failed_qr_start_restores_storage_client(monkeypatch):
    from backend.api import auth as auth_mod
    from backend.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 12345)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "a" * 32)

    calls = {"reconnect": 0}

    async def fake_disconnect():
        return None

    async def fake_reconnect():
        calls["reconnect"] += 1

    monkeypatch.setattr(auth_mod.storage_client, "disconnect", fake_disconnect)
    monkeypatch.setattr(auth_mod.storage_client, "reconnect", fake_reconnect)

    class FailingTelegramClient:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self):
            raise ConnectionError("simulated outage")

        async def disconnect(self):
            return None

        def is_connected(self):
            return False

    monkeypatch.setattr(auth_mod, "TelegramClient", FailingTelegramClient)

    res = client.post("/api/v1/auth/qr/start")
    assert res.status_code == 503
    assert calls["reconnect"] >= 1, "failed QR start must best-effort restore the storage client"


# ---------------------------------------------------------------------------
# B6: NAS stream_bytes must raise for a missing file instead of silently
# yielding an empty stream.
# ---------------------------------------------------------------------------

def test_b6_nas_stream_bytes_missing_file_raises(tmp_path):
    from backend.storage.adapters.nas import NASStorageAdapter

    adapter = NASStorageAdapter(nas_root=tmp_path)
    (tmp_path / "present.bin").write_bytes(b"data")

    def collect(uri):
        async def run():
            out = []
            async for c in adapter.stream_bytes(uri, offset=0, limit=4):
                out.append(c)
            return out
        return asyncio.run(run())

    assert b"".join(collect("present.bin")) == b"data"
    with pytest.raises(FileNotFoundError):
        collect("missing.bin")


# ---------------------------------------------------------------------------
# B9: moov inspection must decrypt CTR-encrypted probes before parsing.
# ---------------------------------------------------------------------------

def test_b9_inspect_mp4_decrypts_ctr_probes(monkeypatch):
    import struct
    from backend.streaming import engine as engine_mod
    from backend.crypto.cipher import aes_ctr_encrypt

    key = bytes(range(32))
    nonce = "0123456789abcdef"
    ftyp = struct.pack(">I4s", 8, b"ftyp")
    moov_payload = b"m" * 64
    moov = struct.pack(">I4s", 8 + len(moov_payload), b"moov") + moov_payload
    plaintext = ftyp + moov + b"\x00" * (256 * 1024)
    ciphertext = aes_ctr_encrypt(plaintext, key, nonce, aligned_byte_offset=0)

    async def fake_stream_part_source(part, offset, limit, chunk_size, storage_client):
        yield ciphertext[offset:offset + limit]

    monkeypatch.setattr(engine_mod, "stream_part_source", fake_stream_part_source)

    parts = [{"byte_offset": 0, "part_size_bytes": len(ciphertext)}]

    def inspect(**kwargs):
        return asyncio.run(
            engine_mod.MP4MoovResolver.inspect_mp4(parts, len(ciphertext), None, **kwargs)
        )

    info = inspect(encryption="ctr", nonce=nonce, key=key)
    assert info["is_mp4"] is True
    assert info["has_moov"] is True
    assert info["faststart"] is True

    # Same bytes without decryption are (correctly) not recognized as MP4 —
    # this is the misreport the fix removes for encrypted files.
    info_raw = inspect()
    assert info_raw["has_moov"] is False


# ---------------------------------------------------------------------------
# B16: bracketed IPv6 loopback Host headers must pass host validation.
# ---------------------------------------------------------------------------

def test_b16_ipv6_host_headers_accepted():
    from starlette.requests import Request
    from fastapi import HTTPException
    from backend.streaming.router import verify_loopback_security

    # Global host-header middleware (main.py)
    r_ok = client.get("/api/v1/health", headers={"Host": "[::1]:8000"})
    assert r_ok.status_code == 200, "bracketed IPv6 loopback Host must be trusted"
    r_evil = client.get("/api/v1/health", headers={"Host": "evil.com"})
    assert r_evil.status_code == 403

    # /virtual-stream loopback check (router.py)
    def make_req(host, client_host):
        return Request({
            "type": "http",
            "headers": [(b"host", host.encode())],
            "client": (client_host, 1234),
        })

    verify_loopback_security(make_req("[::1]:8000", "::1"))
    verify_loopback_security(make_req("127.0.0.1:8000", "127.0.0.1"))
    with pytest.raises(HTTPException):
        verify_loopback_security(make_req("evil.com", "127.0.0.1"))


# ---------------------------------------------------------------------------
# B18: a boundary-aligned limit must not fetch one extra chunk or yield an
# empty b"" chunk.
# ---------------------------------------------------------------------------

def test_b18_boundary_limit_exact_chunk_fetch(monkeypatch):
    CHUNK = 512 * 1024

    class FakeClient:
        def __init__(self):
            self.fetches = 0

        def is_connected(self):
            return True

        async def iter_download(self, media, offset=0, request_size=0, chunk_size=0):
            for _ in range(16):  # 8 MiB document
                self.fetches += 1
                yield b"x" * CHUNK

    fake = FakeClient()
    monkeypatch.setattr(storage_client, "_is_connected", True)
    monkeypatch.setattr(storage_client, "client", fake)
    monkeypatch.setattr(
        storage_client, "_media_cache", {("-100123456789", 9001): object()}
    )

    def collect(limit):
        async def run():
            out = []
            async for c in storage_client.stream_part_bytes(
                9001, channel_id="-100123456789", offset=0, limit=limit, chunk_size=CHUNK
            ):
                out.append(c)
            return out
        return asyncio.run(run())

    # limit == chunk_size -> direct branch: exactly one fetch, no empty chunk
    chunks = collect(CHUNK)
    assert chunks == [b"x" * CHUNK]
    assert fake.fetches == 1

    # limit == 2x chunk_size -> pipeline branch: exactly two fetches
    fake.fetches = 0
    chunks2 = collect(2 * CHUNK)
    assert sum(len(c) for c in chunks2) == 2 * CHUNK
    assert all(len(c) > 0 for c in chunks2)
    assert fake.fetches == 2


# ---------------------------------------------------------------------------
# Production hardening residuals: C-02 (completed files must never be erased
# via the incomplete-upload cleanup) and C-04 (chunks cannot modify a
# completed file).
# ---------------------------------------------------------------------------

def _seed_completed_file(fid: str, msg_id: int) -> None:
    from backend.db.connection import get_db, init_db

    async def seed():
        await init_db()
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO files (
                    id, filename, mime_type, file_category, size_bytes, sha256,
                    total_parts, upload_status, storage_backend
                ) VALUES (?, 'done.bin', 'application/octet-stream', 'other', 10, 'sha', 1, 'completed', 'telegram')
                """,
                (fid,),
            )
            await db.execute(
                """
                INSERT INTO file_parts (
                    id, file_id, part_index, byte_offset, part_size_bytes,
                    part_sha256, telegram_message_id, telegram_channel_id,
                    storage_backend, storage_uri
                ) VALUES ('vp_cx1', ?, 0, 0, 10, 'psha', ?, '-100123456789', 'telegram', ?)
                """,
                (fid, msg_id, f"tg://-100123456789/{msg_id}"),
            )
            await db.commit()

    asyncio.run(seed())


def test_c02_incomplete_cleanup_refuses_completed_file(monkeypatch):
    from backend.db.connection import get_db

    fid = "v_cc00000000000000000000000000c02a"
    _seed_completed_file(fid, 7101)

    deleted: list = []

    async def record_delete(parts):
        deleted.extend(parts)
        return len(parts)

    monkeypatch.setattr(storage_client, "delete_parts", record_delete)

    res = client.delete(f"/api/v1/files/{fid}/incomplete")
    assert res.status_code == 409
    assert deleted == []

    async def still_there():
        async with get_db() as db:
            cur = await db.execute("SELECT id FROM files WHERE id = ?", (fid,))
            return await cur.fetchone() is not None

    assert asyncio.run(still_there()) is True


def test_c04_chunk_upload_refuses_completed_file():
    fid = "v_cc00000000000000000000000000c04b"
    _seed_completed_file(fid, 7102)

    res = client.post(
        "/api/v1/upload/chunk",
        data={
            "file_id": fid, "part_index": 0, "total_parts": 1,
            "byte_offset": 0, "filename": "done.bin", "total_size": 10,
            "encryption": "none",
        },
        files={"chunk": ("done.bin", b"0123456789", "application/octet-stream")},
    )
    assert res.status_code == 409
    assert "completed" in res.json()["detail"].lower()
