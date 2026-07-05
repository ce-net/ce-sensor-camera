"""Unit tests for the camera source seam, PNG frame generator, and driver auto-selection."""

from __future__ import annotations

import struct
import zlib

from camera.source import (
    QUALITY_LADDER,
    Frame,
    MockCamera,
    detect_v4l2_camera,
    make_png,
    select_camera,
)


def _assert_valid_png(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    i, saw_iend = 8, False
    while i < len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        tag = data[i + 4:i + 8]
        chunk = data[i + 4:i + 8 + length]
        crc = struct.unpack(">I", data[i + 8 + length:i + 12 + length])[0]
        assert crc == (zlib.crc32(chunk) & 0xFFFFFFFF)
        saw_iend = saw_iend or tag == b"IEND"
        i += 12 + length
    assert saw_iend
    return width, height


def test_make_png_is_valid_and_sized():
    assert _assert_valid_png(make_png(160, 120, 0)) == (160, 120)


def test_quality_ladder_shape():
    assert set(QUALITY_LADDER) == {"low", "medium", "high"}
    assert QUALITY_LADDER["low"][0] < QUALITY_LADDER["medium"][0] < QUALITY_LADDER["high"][0]


def test_mock_camera_dims_match_ladder():
    cam = MockCamera()
    for q in ("low", "medium"):  # keep tests fast; skip generating the largest frame
        f = cam.capture(q)
        assert isinstance(f, Frame)
        assert (f.width, f.height) == (QUALITY_LADDER[q][0], QUALITY_LADDER[q][1])
        assert f.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_mock_camera_frames_advance_and_differ():
    cam = MockCamera()
    a, b = cam.capture("low").data, cam.capture("low").data
    assert cam.seq == 2 and a != b


def test_unknown_quality_falls_back_to_default():
    f = MockCamera().capture("nonsense")
    assert (f.width, f.height) == (QUALITY_LADDER["medium"][0], QUALITY_LADDER["medium"][1])


def test_detect_v4l2_returns_none_without_device():
    assert detect_v4l2_camera("/dev/video-nope-999") is None


def test_select_camera_mock_and_auto_fallback():
    assert isinstance(select_camera("mock"), MockCamera)
    # auto with a nonexistent device must fall back to mock, never raise
    assert isinstance(select_camera("auto", "/dev/video-nope-999"), MockCamera)
