"""Unit tests for the camera source seam and the PNG frame generator."""

from __future__ import annotations

import struct
import zlib

import pytest

from camera.source import QUALITY_LADDER, Frame, MockCamera, V4l2Camera, make_png


def _assert_valid_png(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # IHDR is the first chunk: length(4) + "IHDR"(4) + data(13) + crc(4).
    assert data[12:16] == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    # Every chunk's CRC must be valid — walk them.
    i = 8
    saw_iend = False
    while i < len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        tag = data[i + 4:i + 8]
        chunk = data[i + 4:i + 8 + length]
        crc = struct.unpack(">I", data[i + 8 + length:i + 12 + length])[0]
        assert crc == (zlib.crc32(chunk) & 0xFFFFFFFF)
        if tag == b"IEND":
            saw_iend = True
        i += 12 + length
    assert saw_iend
    return width, height


def test_make_png_is_valid_and_sized():
    w, h = _assert_valid_png(make_png(160, 120, 0))
    assert (w, h) == (160, 120)


def test_mock_camera_respects_quality_ladder():
    cam = MockCamera()
    for q, (w, h, _fps) in QUALITY_LADDER.items():
        f = cam.capture(q)
        assert isinstance(f, Frame)
        assert (f.width, f.height) == (w, h)
        assert _assert_valid_png(f.data) == (w, h)


def test_mock_camera_frames_advance_and_differ():
    cam = MockCamera()
    a = cam.capture("low").data
    b = cam.capture("low").data
    assert cam.seq == 2
    assert a != b  # seq encoded into the image -> frames differ


def test_unknown_quality_falls_back_to_default():
    f = MockCamera().capture("nonsense")
    assert (f.width, f.height) == (QUALITY_LADDER["medium"][0], QUALITY_LADDER["medium"][1])


def test_v4l2_is_an_unimplemented_plugin_point():
    with pytest.raises(NotImplementedError):
        V4l2Camera().capture("low")
