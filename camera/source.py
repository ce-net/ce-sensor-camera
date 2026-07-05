"""The hardware seam for the camera sensor.

A camera ceapp is JUST a frame source with a small control surface. Everything above this
file is hardware-agnostic, so swapping the mock for a real camera (v4l2 ``/dev/video0`` on
the UNO Q, or a USB/CSI cam) is a one-class change — plug and play — with no edits to the
service, the control protocol, the wire schema, or any consumer.

The mock emits real, viewable PNG frames generated with the standard library only (no
Pillow, no OpenCV) so the pipeline and the digital twin are provable with zero hardware.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Named quality ladder Leif asked for ("lower / increase quality"). Each maps to a
# resolution and a nominal frame rate; consumers read the labels, not raw numbers.
QUALITY_LADDER: dict[str, tuple[int, int, int]] = {
    "low": (160, 120, 2),
    "medium": (320, 240, 5),
    "high": (640, 480, 10),
}
DEFAULT_QUALITY = "medium"


@dataclass(frozen=True)
class Frame:
    """One captured frame: encoded image bytes plus its format and dimensions."""

    data: bytes
    fmt: str
    width: int
    height: int


@runtime_checkable
class Camera(Protocol):
    """Captures a frame at a named quality. The only hardware-specific interface."""

    def capture(self, quality: str) -> Frame: ...


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def make_png(width: int, height: int, seq: int) -> bytes:
    """A small, valid RGB PNG: a gradient whose blue channel encodes ``seq`` so successive
    frames visibly differ. Gradients compress well, so frames stay light on the wire."""
    row_prefix = b"\x00"  # PNG filter type 0 (none)
    blue = (seq * 8) & 0xFF
    raw = bytearray()
    wdiv = max(width - 1, 1)
    hdiv = max(height - 1, 1)
    for y in range(height):
        g = (y * 255) // hdiv
        raw += row_prefix
        row = bytearray()
        for x in range(width):
            r = (x * 255) // wdiv
            row += bytes((r, g, blue))
        raw += row
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, colour type 2 (RGB)
    idat = zlib.compress(bytes(raw), 6)
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


class MockCamera:
    """Synthetic PNG frames — no hardware. Deterministic given the sequence number, so a
    live demo shows a moving image and tests are reproducible."""

    def __init__(self) -> None:
        self.seq = 0

    def capture(self, quality: str) -> Frame:
        w, h, _ = QUALITY_LADDER.get(quality, QUALITY_LADDER[DEFAULT_QUALITY])
        data = make_png(w, h, self.seq)
        self.seq += 1
        return Frame(data=data, fmt="png", width=w, height=h)


class V4l2Camera:
    """Placeholder for a real camera (v4l2 ``/dev/video0`` / USB / CSI).

    Wiring the real device is isolated here: implement :meth:`capture` to grab a frame at
    the requested resolution and return it JPEG/PNG-encoded, then swap this in for
    :class:`MockCamera` in ``main.py``. Nothing else in the app or the mesh changes.
    """

    def __init__(self, device: str = "/dev/video0") -> None:
        self.device = device

    def capture(self, quality: str) -> Frame:  # pragma: no cover - hardware path
        raise NotImplementedError(
            f"V4l2Camera is a hardware plug-in point: implement capture() against "
            f"{self.device} for a real camera (e.g. via v4l2/ffmpeg)."
        )
