import asyncio
import logging
import struct
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, AsyncIterator, Any

logger = logging.getLogger("vayom.streaming")

try:
    from ..crypto.cipher import aes_ctr_decrypt
except Exception:
    aes_ctr_decrypt = None

# Cryptographic and binary invariants from update.md Section 0 & Section 3
DEFAULT_CHUNK_SIZE: int = 512 * 1024       # 512 KiB (multiple of 4096, divides 1 MiB)
DEFAULT_MAX_206_BYTES: int = 16 * 1024 * 1024  # 16 MiB default capping for HTTP 206
MAX_CACHE_CHUNKS: int = 128                # 128 * 512 KiB = 64 MiB max RAM cache


@dataclass
class RangeSpec:
    start: int
    end: int
    total_size: int
    content_length: int
    is_range: bool
    is_satisfiable: bool
    is_capped: bool = False

    @property
    def content_range_header(self) -> str:
        """Returns truthful Content-Range header value."""
        if not self.is_satisfiable:
            return f"bytes */{self.total_size}"
        return f"bytes {self.start}-{self.end}/{self.total_size}"


def parse_and_clamp_range(
    range_header: Optional[str],
    total_size: int,
    max_chunk_bytes: int = DEFAULT_MAX_206_BYTES
) -> RangeSpec:
    """
    Parses and clamps HTTP Range requests according to RFC 7233 / 9110 and update.md invariants:
    - Normalizes: bytes=a-b, bytes=a-, bytes=-n (suffix byte ranges)
    - Returns 416 Range Not Satisfiable if start >= total_size or start > end
    - Ignores malformed or multi-range headers, falling back to 200 OK full content
    - Caps HTTP 206 responses to max_chunk_bytes (default 16 MiB) with truthful Content-Range
    """
    if total_size <= 0:
        # Requesting a range on a 0-byte file is unsatisfiable per RFC 9110
        if range_header and range_header.strip():
            return RangeSpec(
                start=0,
                end=0,
                total_size=0,
                content_length=0,
                is_range=True,
                is_satisfiable=False,
                is_capped=False
            )
        return RangeSpec(
            start=0,
            end=0,
            total_size=0,
            content_length=0,
            is_range=False,
            is_satisfiable=True,
            is_capped=False
        )

    # If no Range header provided, return full representation (200 OK)
    if not range_header or not range_header.strip():
        return RangeSpec(
            start=0,
            end=total_size - 1,
            total_size=total_size,
            content_length=total_size,
            is_range=False,
            is_satisfiable=True,
            is_capped=False
        )

    header = range_header.strip()
    if not header.startswith("bytes="):
        # Malformed header -> fallback to full content 200 OK
        return RangeSpec(
            start=0,
            end=total_size - 1,
            total_size=total_size,
            content_length=total_size,
            is_range=False,
            is_satisfiable=True,
            is_capped=False
        )

    ranges_str = header[len("bytes="):].strip()

    # Reject multi-range (e.g. "bytes=0-10,20-30") per update.md specification
    if "," in ranges_str:
        return RangeSpec(
            start=0,
            end=total_size - 1,
            total_size=total_size,
            content_length=total_size,
            is_range=False,
            is_satisfiable=True,
            is_capped=False
        )

    parts = ranges_str.split("-")
    if len(parts) != 2:
        return RangeSpec(
            start=0,
            end=total_size - 1,
            total_size=total_size,
            content_length=total_size,
            is_range=False,
            is_satisfiable=True,
            is_capped=False
        )

    start_str, end_str = parts[0].strip(), parts[1].strip()

    # Case 1: Suffix range `bytes=-n` (last n bytes of the file)
    if not start_str and end_str:
        try:
            suffix_len = int(end_str)
            if suffix_len <= 0:
                # 0 suffix bytes is not satisfiable
                return RangeSpec(0, 0, total_size, 0, is_range=True, is_satisfiable=False)
            start = max(0, total_size - suffix_len)
            end = total_size - 1
        except ValueError:
            return RangeSpec(0, total_size - 1, total_size, total_size, is_range=False, is_satisfiable=True)

    # Case 2: Open-ended range `bytes=a-`
    elif start_str and not end_str:
        try:
            start = int(start_str)
            if start >= total_size or start < 0:
                return RangeSpec(start, total_size - 1, total_size, 0, is_range=True, is_satisfiable=False)
            end = total_size - 1
        except ValueError:
            return RangeSpec(0, total_size - 1, total_size, total_size, is_range=False, is_satisfiable=True)

    # Case 3: Explicit range `bytes=a-b`
    elif start_str and end_str:
        try:
            start = int(start_str)
            end_val = int(end_str)
            if start < 0 or start >= total_size or start > end_val:
                return RangeSpec(start, end_val, total_size, 0, is_range=True, is_satisfiable=False)
            end = min(end_val, total_size - 1)
        except ValueError:
            return RangeSpec(0, total_size - 1, total_size, total_size, is_range=False, is_satisfiable=True)

    else:
        # Invalid format `bytes=-`
        return RangeSpec(0, total_size - 1, total_size, total_size, is_range=False, is_satisfiable=True)

    # Invariant: Cap single 206 responses to max_chunk_bytes (e.g. 16 MiB)
    # The client/player will send subsequent range requests for remaining bytes
    is_capped = False
    requested_length = end - start + 1
    if requested_length > max_chunk_bytes:
        end = start + max_chunk_bytes - 1
        is_capped = True

    content_length = end - start + 1
    return RangeSpec(
        start=start,
        end=end,
        total_size=total_size,
        content_length=content_length,
        is_range=True,
        is_satisfiable=True,
        is_capped=is_capped
    )


class LRUChunkCache:
    """
    In-memory bounded LRU Chunk Cache with single-flight request coalescing.
    Prevents duplicate upstream fetches to Telegram MTProto when concurrent requests
    (e.g., audio/video demuxers or parallel scrubbers) request the same byte chunk.
    """
    def __init__(self, max_chunks: int = MAX_CACHE_CHUNKS):
        self.max_chunks = max_chunks
        self._cache: OrderedDict[Tuple[int, int], bytes] = OrderedDict()
        self._in_flight: Dict[Tuple[int, int], asyncio.Future[bytes]] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(
        self,
        key: Tuple[int, int],
        fetcher_coro_fn
    ) -> bytes:
        """
        Retrieves chunk from cache, joins an in-flight fetch if one is already running,
        or triggers a fresh fetch and populates the cache.
        """
        # 1. Fast cache check
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

            # 2. Check single-flight table
            if key in self._in_flight:
                fut = self._in_flight[key]
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._in_flight[key] = fut
                # We are the designated fetcher
                asyncio.create_task(self._execute_fetch(key, fetcher_coro_fn, fut))

        return await fut

    async def _execute_fetch(self, key: Tuple[int, int], fetcher_coro_fn, fut: asyncio.Future[bytes]):
        try:
            chunk_data = await fetcher_coro_fn()
            async with self._lock:
                self._cache[key] = chunk_data
                self._cache.move_to_end(key)
                if len(self._cache) > self.max_chunks:
                    self._cache.popitem(last=False)  # Evict LRU
                self._in_flight.pop(key, None)
            if not fut.done():
                fut.set_result(chunk_data)
        except Exception as e:
            async with self._lock:
                self._in_flight.pop(key, None)
            if not fut.done():
                fut.set_exception(e)

    async def clear(self):
        async with self._lock:
            self._cache.clear()
            self._in_flight.clear()

    def size(self) -> int:
        return len(self._cache)


class MP4AtomInfo:
    def __init__(self, atom_type: str, offset: int, size: int, header_size: int):
        self.atom_type = atom_type
        self.offset = offset
        self.size = size
        self.header_size = header_size

    def to_dict(self) -> dict:
        return {
            "type": self.atom_type,
            "offset": self.offset,
            "size": self.size,
            "header_size": self.header_size
        }


class MP4MoovResolver:
    """
    Inspects ISO Base Media File Format (MP4/MOV) boxes.
    Detects whether an MP4 container has FastStart ('moov' atom at front)
    or is trailing ('moov' atom at tail, typical of non-faststart / direct camera exports).
    Resolves the exact position and contents of the 'moov' atom without buffering
    gigabytes of intermediate 'mdat' frames into memory.
    """
    @staticmethod
    def parse_box_header(data: bytes, offset: int = 0) -> Optional[MP4AtomInfo]:
        """Parses a single box header at data[offset:]. Supports 32-bit and 64-bit extended sizes."""
        if len(data) - offset < 8:
            return None

        size_32 = struct.unpack(">I", data[offset:offset + 4])[0]
        try:
            atom_type = data[offset + 4:offset + 8].decode("ascii", errors="replace")
        except Exception:
            return None

        header_size = 8
        if size_32 == 1:
            # 64-bit extended size
            if len(data) - offset < 16:
                return None
            size = struct.unpack(">Q", data[offset + 8:offset + 16])[0]
            header_size = 16
        elif size_32 == 0:
            # Box extends to end of file
            size = len(data) - offset
        else:
            size = size_32

        return MP4AtomInfo(atom_type, offset, size, header_size)

    @classmethod
    def parse_atoms(cls, data: bytes, base_offset: int = 0) -> List[MP4AtomInfo]:
        """Iterates through top-level atoms in a buffer."""
        atoms = []
        offset = 0
        while offset < len(data):
            box = cls.parse_box_header(data, offset)
            if not box or box.size <= 0:
                break
            box.offset += base_offset
            atoms.append(box)
            offset += box.size
        return atoms

    @classmethod
    async def inspect_mp4(
        cls,
        parts: List[dict],
        total_size: int,
        storage_client
    ) -> Dict[str, Any]:
        """
        Scans initial bytes for 'moov' atom. If absent, probes the tail of the file
        (up to 2 MB from EOF) to locate and parse the trailing 'moov' atom.
        """
        if total_size < 16:
            return {"is_mp4": False, "has_moov": False, "faststart": False}

        # 1. Fetch initial 64 KB to scan for ftyp, moov, mdat
        probe_head_size = min(64 * 1024, total_size)
        head_bytes = bytearray()

        # Read first probe_head_size bytes from Part 0
        part0 = parts[0]
        async for chunk in storage_client.stream_part_bytes(
            message_id=part0["telegram_message_id"],
            channel_id=part0.get("telegram_channel_id"),
            file_id=part0.get("telegram_file_id"),
            access_hash=part0.get("telegram_access_hash"),
            offset=0,
            limit=probe_head_size
        ):
            head_bytes.extend(chunk)

        head_bytes = bytes(head_bytes)
        atoms = cls.parse_atoms(head_bytes)

        has_ftyp = any(a.atom_type == "ftyp" for a in atoms)
        moov_atom = next((a for a in atoms if a.atom_type == "moov"), None)

        if moov_atom:
            return {
                "is_mp4": True,
                "has_moov": True,
                "faststart": True,
                "moov_offset": moov_atom.offset,
                "moov_size": moov_atom.size,
                "atoms": [a.to_dict() for a in atoms]
            }

        # 2. 'moov' not in initial 64KB -> Check trailing segment (up to 2 MB from EOF)
        tail_probe_size = min(2 * 1024 * 1024, total_size)
        tail_start = total_size - tail_probe_size

        # Find part containing tail_start
        tail_bytes = bytearray()
        for p in parts:
            p_start = p["byte_offset"]
            p_end = p_start + p["part_size_bytes"] - 1
            if p_end < tail_start:
                continue

            part_slice_start = max(0, tail_start - p_start)
            part_slice_len = min(p["part_size_bytes"] - part_slice_start, tail_probe_size - len(tail_bytes))
            if part_slice_len <= 0:
                continue

            async for chunk in storage_client.stream_part_bytes(
                message_id=p["telegram_message_id"],
                channel_id=p.get("telegram_channel_id"),
                file_id=p.get("telegram_file_id"),
                access_hash=p.get("telegram_access_hash"),
                offset=part_slice_start,
                limit=part_slice_len
            ):
                tail_bytes.extend(chunk)

        tail_bytes = bytes(tail_bytes)

        # Search for b'moov' signature in tail bytes
        moov_idx = tail_bytes.find(b"moov")
        if moov_idx >= 4:
            # 4 bytes before 'moov' is the 32-bit size
            box_offset_in_tail = moov_idx - 4
            global_moov_offset = tail_start + box_offset_in_tail
            moov_box = cls.parse_box_header(tail_bytes, box_offset_in_tail)
            moov_size = moov_box.size if moov_box else (total_size - global_moov_offset)

            return {
                "is_mp4": True,
                "has_moov": True,
                "faststart": False,
                "trailing_moov": True,
                "moov_offset": global_moov_offset,
                "moov_size": moov_size,
                "tail_probe_bytes": len(tail_bytes)
            }

        return {
            "is_mp4": has_ftyp,
            "has_moov": False,
            "faststart": False,
            "atoms": [a.to_dict() for a in atoms]
        }


class StreamingEngine:
    """
    Production-grade streaming engine for high-bitrate 4K streaming and memory optimization.
    Features:
    - 512 KiB aligned chunk fetching (multiple of 4096 dividing 1 MiB)
    - Sliding window memory consumption (< 100 MB baseline regardless of multi-gigabyte file sizes)
    - LRU Chunk Cache with single-flight request coalescing
    - Clean socket tear-down on client disconnect (asyncio.CancelledError)
    - Guaranteed truthful Content-Length & Content-Range
    """
    def __init__(self, cache_size: int = MAX_CACHE_CHUNKS, chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.chunk_size = chunk_size
        self.cache = LRUChunkCache(max_chunks=cache_size)
        self.moov_resolver = MP4MoovResolver()

    async def stream_range(
        self,
        parts: List[dict],
        total_size: int,
        range_spec: RangeSpec,
        storage_client,
        encryption: str = "none",
        nonce: Optional[str] = None,
        key: Optional[bytes] = None
    ) -> AsyncIterator[bytes]:
        """
        Asynchronously streams the requested byte range slice-by-slice across one or more file parts.
        Guarantees that memory consumption stays strictly bounded by the sliding window buffer.
        """
        if not range_spec.is_satisfiable or range_spec.content_length <= 0:
            return

        target_start = range_spec.start
        target_end = range_spec.end
        bytes_yielded = 0

        try:
            sorted_parts = sorted(parts, key=lambda p: p.get("byte_offset", 0))
            for part in sorted_parts:
                p_start = part["byte_offset"]
                p_end = p_start + part["part_size_bytes"] - 1

                # Skip parts that do not intersect [target_start, target_end]
                if p_end < target_start or p_start > target_end:
                    continue

                # Determine slice coordinates within this specific part
                slice_start = max(0, target_start - p_start)
                slice_end = min(part["part_size_bytes"] - 1, target_end - p_start)
                slice_len = slice_end - slice_start + 1

                if slice_len <= 0:
                    continue

                # Stream using chunk-aligned fetching with LRU cache & single-flight
                curr_offset = slice_start
                while curr_offset <= slice_end:
                    # Invariant: fetch aligned to 512 KB boundary
                    chunk_aligned_offset = (curr_offset // self.chunk_size) * self.chunk_size
                    offset_within_chunk = curr_offset - chunk_aligned_offset
                    chunk_limit = min(self.chunk_size, part["part_size_bytes"] - chunk_aligned_offset)

                    msg_id = part["telegram_message_id"]
                    cache_key = (msg_id, chunk_aligned_offset)

                    # Fetch chunk via LRU cache (with single-flight coalescing)
                    async def _fetch_upstream() -> bytes:
                        buf = bytearray()
                        async for raw_block in storage_client.stream_part_bytes(
                            message_id=msg_id,
                            channel_id=part.get("telegram_channel_id"),
                            file_id=part.get("telegram_file_id"),
                            access_hash=part.get("telegram_access_hash"),
                            offset=chunk_aligned_offset,
                            limit=chunk_limit,
                            chunk_size=min(self.chunk_size, 64 * 1024)
                        ):
                            buf.extend(raw_block)
                        return bytes(buf)

                    chunk_bytes = await self.cache.get_or_fetch(cache_key, _fetch_upstream)
                    if not chunk_bytes:
                        break

                    # Invariant 1: Decrypt chunk if encrypted with AES-256-CTR
                    if encryption == "ctr" and nonce and key and aes_ctr_decrypt:
                        chunk_bytes = aes_ctr_decrypt(
                            chunk_bytes,
                            key,
                            nonce,
                            aligned_byte_offset=chunk_aligned_offset
                        )

                    # Slice out the specific sub-segment needed for this range
                    needed_len = min(len(chunk_bytes) - offset_within_chunk, slice_end - curr_offset + 1)
                    if needed_len <= 0:
                        break

                    data_to_yield = chunk_bytes[offset_within_chunk:offset_within_chunk + needed_len]
                    yield data_to_yield

                    bytes_yielded += len(data_to_yield)
                    curr_offset += len(data_to_yield)

            # Invariant: check if upstream yielded fewer bytes than promised
            if bytes_yielded < range_spec.content_length:
                logger.warning(
                    f"Streaming short body detected: yielded {bytes_yielded} / promised {range_spec.content_length}. "
                    "Aborting stream to prevent client corruption."
                )
                raise ConnectionResetError("Stream truncated mid-body: fewer bytes returned than promised.")

        except asyncio.CancelledError:
            logger.info("Client disconnected during video stream. Terminating upstream pull cleanly.")
            raise


# Global singleton streaming engine instance
streaming_engine = StreamingEngine()
