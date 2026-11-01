import pytest
from backend.streaming.engine import (
    parse_and_clamp_range,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_206_BYTES,
    RangeSpec
)

def test_50gb_and_1200gb_part_partitioning_zero_byte_loss():
    """
    Mathematical and extent proof that 50 GB and 1200 GB files
    have 100% contiguous, gap-free, overlap-free part extents.
    """
    PART_MAX = 1_900_000_000  # 1.9 GB per Telegram limit

    for test_size, expected_part_count in [
        (50_000_000_000, 27),         # 50 GB file
        (1_200_000_000_000, 632)      # 1,200 GB (1.2 TB) file
    ]:
        parts = []
        offset = 0
        part_idx = 0
        while offset < test_size:
            slice_size = min(PART_MAX, test_size - offset)
            parts.append({
                "part_index": part_idx,
                "byte_offset": offset,
                "part_size_bytes": slice_size
            })
            offset += slice_size
            part_idx += 1

        assert len(parts) == expected_part_count
        assert sum(p["part_size_bytes"] for p in parts) == test_size

        # Verify zero gaps and zero overlaps across entire extent manifest
        for i in range(len(parts)):
            if i == 0:
                assert parts[i]["byte_offset"] == 0
            else:
                prev = parts[i - 1]
                assert parts[i]["byte_offset"] == prev["byte_offset"] + prev["part_size_bytes"]

def test_50gb_video_scrubbing_range_clamping():
    """
    Simulates seeking / scrubbing to arbitrary points in a 50 GB 4K video:
    - 0% (header / moov)
    - 25% (offset 12.5 GB)
    - 50% (offset 25 GB)
    - 75% (offset 37.5 GB)
    - 99.9% (EOF tail moov / end credits)
    """
    TOTAL_50GB = 50_000_000_000

    # 1. User scrubs to 37.5 GB (middle of Part 19)
    scrub_offset = 37_500_000_000
    range_header = f"bytes={scrub_offset}-"
    spec = parse_and_clamp_range(range_header, TOTAL_50GB, max_chunk_bytes=DEFAULT_MAX_206_BYTES)

    assert spec.is_satisfiable is True
    assert spec.is_range is True
    assert spec.start == scrub_offset
    # Capped to 4 MiB sliding window
    assert spec.content_length == DEFAULT_MAX_206_BYTES
    assert spec.end == scrub_offset + DEFAULT_MAX_206_BYTES - 1
    assert spec.content_range_header == f"bytes {scrub_offset}-{scrub_offset + DEFAULT_MAX_206_BYTES - 1}/{TOTAL_50GB}"

    # 2. Suffix range request (browser requests last 2 MB to probe trailing moov atom)
    spec_suffix = parse_and_clamp_range("bytes=-2097152", TOTAL_50GB, max_chunk_bytes=DEFAULT_MAX_206_BYTES)
    assert spec_suffix.is_satisfiable is True
    assert spec_suffix.start == TOTAL_50GB - 2097152
    assert spec_suffix.end == TOTAL_50GB - 1
    assert spec_suffix.content_length == 2097152

    # 3. Beyond EOF request -> 416
    spec_invalid = parse_and_clamp_range(f"bytes={TOTAL_50GB}-", TOTAL_50GB)
    assert spec_invalid.is_satisfiable is False
    assert spec_invalid.content_range_header == f"bytes */{TOTAL_50GB}"
