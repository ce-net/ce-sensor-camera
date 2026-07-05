"""Unit tests for the camera frame wire schema."""

from __future__ import annotations

import pytest

from camera.frame import FRAME_SCHEMA, decode_frame, encode_frame, frame_bytes
from camera.source import Frame


def test_encode_decode_roundtrip_recovers_image_bytes():
    frame = Frame(data=b"\x89PNG-fake-bytes", fmt="png", width=160, height=120)
    payload = encode_frame("aa" * 32, "cam-1", 7, 1720000000.0, "low", frame)
    decoded = decode_frame(payload)
    assert decoded["schema"] == FRAME_SCHEMA
    assert decoded["seq"] == 7
    assert decoded["quality"] == "low"
    assert (decoded["width"], decoded["height"]) == (160, 120)
    assert frame_bytes(decoded) == b"\x89PNG-fake-bytes"


def test_decode_rejects_wrong_schema():
    with pytest.raises(ValueError):
        decode_frame(b'{"schema":"other/9"}')
