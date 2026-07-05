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
    frames visibly differ. Gradients compress well, so frames stay light on the wire.

    Built with C-level strided slice assignment (not a per-pixel Python loop) so even a
    640x480 frame is generated in a few milliseconds — fast for the stream and instant tests.
    """
    blue = (seq * 8) & 0xFF
    wdiv = max(width - 1, 1)
    hdiv = max(height - 1, 1)
    reds = bytes((x * 255) // wdiv for x in range(width))  # red ramp per column (constant across rows)
    blues = bytes([blue]) * width
    raw = bytearray()
    for y in range(height):
        row = bytearray(width * 3)
        row[0::3] = reds
        row[1::3] = bytes([(y * 255) // hdiv]) * width      # green: constant within the row
        row[2::3] = blues
        raw.append(0)          # PNG filter type 0 (none)
        raw.extend(row)
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
    """A real camera on a Linux ``/dev/video*`` device (USB webcam or CSI on the UNO Q).

    Grabs one JPEG frame at the requested resolution via ``ffmpeg`` (preferred) or
    ``fswebcam`` — both common, no Python packages. This is the plug-in that makes the
    identical camera ceapp use real hardware when a camera is present; nothing above it moves.
    """

    def __init__(self, device: str = "/dev/video0") -> None:
        self.device = device

    def _command(self, w: int, h: int):
        import shutil
        if shutil.which("ffmpeg"):
            return ["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-video_size", f"{w}x{h}",
                    "-i", self.device, "-frames:v", "1", "-f", "mjpeg", "pipe:1"]
        if shutil.which("fswebcam"):
            return ["fswebcam", "-d", self.device, "-r", f"{w}x{h}", "--no-banner", "-q", "-"]
        return None

    def capture(self, quality: str) -> Frame:
        import subprocess
        w, h, _ = QUALITY_LADDER.get(quality, QUALITY_LADDER[DEFAULT_QUALITY])
        cmd = self._command(w, h)
        if cmd is None:
            raise OSError("no ffmpeg/fswebcam available to capture from " + self.device)
        proc = subprocess.run(cmd, capture_output=True, timeout=10)
        if proc.returncode != 0 or not proc.stdout:
            raise OSError(f"v4l2 capture failed: {proc.stderr[:200].decode('utf-8', 'replace')}")
        return Frame(data=proc.stdout, fmt="jpeg", width=w, height=h)


def detect_v4l2_camera(device: str = "/dev/video0") -> "V4l2Camera | None":
    """Return a real camera if a video device AND a capture tool are present, else None."""
    import os
    import shutil
    if not os.path.exists(device):
        return None
    if not (shutil.which("ffmpeg") or shutil.which("fswebcam")):
        return None
    return V4l2Camera(device)


SOURCE_MODES = ("auto", "mock", "real")


def select_camera(mode: str = "auto", device: str = "/dev/video0") -> Camera:
    """Pick the camera by source mode — switchable on demand (startup env or the live API):
    ``auto`` = real device if present else mock; ``mock`` forces synthetic; ``real`` (alias
    ``v4l2``) requires a real device (raises if none). Real hardware is plug-and-play while an
    end-to-end test can force mock with no camera."""
    mode = (mode or "auto").lower()
    if mode == "mock":
        return MockCamera()
    real = detect_v4l2_camera(device)
    if real is not None:
        return real
    if mode in ("real", "v4l2"):
        raise OSError("no camera at " + device + " (or no ffmpeg/fswebcam)")
    return MockCamera()
