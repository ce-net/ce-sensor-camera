"""The camera frame wire schema, shared by every consumer.

Each frame is an identity block plus the encoded image inline (hex). Inline bytes keep the
stream reliable across nodes today (the cross-node blob-fetch path is a known gap); for
real high-resolution video the same schema swaps ``bytes_hex`` for a blob ``cid`` without
touching consumers.
"""

from __future__ import annotations

import json

from .source import Frame

FRAME_SCHEMA = "ce.sensor.frame/1"


def encode_frame(node: str, instance: str, seq: int, ts: float, quality: str,
                 frame: Frame) -> bytes:
    return json.dumps({
        "schema": FRAME_SCHEMA,
        "sensor": "ce-sensor-camera",
        "node": node,
        "instance": instance,
        "seq": seq,
        "ts": round(ts, 3),
        "quality": quality,
        "format": frame.fmt,
        "width": frame.width,
        "height": frame.height,
        "bytes_hex": frame.data.hex(),
    }, separators=(",", ":")).encode("utf-8")


def decode_frame(payload: bytes) -> dict:
    obj = json.loads(payload.decode("utf-8"))
    if obj.get("schema") != FRAME_SCHEMA:
        raise ValueError(f"unexpected schema: {obj.get('schema')!r}")
    return obj


def frame_bytes(decoded: dict) -> bytes:
    """Recover the raw image bytes from a decoded frame message."""
    return bytes.fromhex(decoded["bytes_hex"])
