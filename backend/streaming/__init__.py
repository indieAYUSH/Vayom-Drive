"""
Vayom Drive Streaming Engine & Memory Optimization Package.
Adheres strictly to update.md Section 3 & Section 0 Binary Invariants.
"""
from .engine import (
    RangeSpec,
    parse_and_clamp_range,
    LRUChunkCache,
    StreamingEngine,
    streaming_engine,
    MP4MoovResolver,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_206_BYTES,
)

__all__ = [
    "RangeSpec",
    "parse_and_clamp_range",
    "LRUChunkCache",
    "StreamingEngine",
    "streaming_engine",
    "MP4MoovResolver",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_MAX_206_BYTES",
]
