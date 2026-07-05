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


class ArduinoCamera:
    """The Arduino UNO Q camera via the App Bricks SDK — the real, SDK-faithful path.

    Uses ``arduino.app_peripherals.camera.Camera`` (github.com/arduino/app-bricks-py), the unified
    Arduino abstraction over USB (V4L), CSI, IP, and WebSocket cameras. A Deltaco USB webcam is a
    generic UVC/V4L device, so it is picked up as the first plugged USB camera by ``Camera(0)``
    (``nth_plugged_camera`` scans ``/dev/v4l/by-id/``, USB before CSI). On the UNO Q this is the
    ONLY correct path: the Qualcomm ISP means a raw ``ffmpeg -f v4l2`` does not work — the SDK
    drives the camera through its GStreamer/libcamera media graph.

    ``capture()`` returns an ``HxWx3`` numpy array in **RGB**. We JPEG-encode with the SDK's own
    ``compress_to_jpeg`` (``arduino.app_utils.image.adjustments``) so colour order matches the SDK
    exactly; if that helper is unavailable we fall back to cv2, converting RGB->BGR first (cv2
    assumes BGR, so encoding RGB directly would swap red and blue). Everything is imported lazily
    so this module still loads — and the tests run — on a Mac without the SDK.
    """

    def __init__(self, source=0) -> None:
        self.source = source
        self._cam = None
        self._quality = None

    def _ensure(self, quality: str):
        w, h, fps = QUALITY_LADDER.get(quality, QUALITY_LADDER[DEFAULT_QUALITY])
        if self._cam is None or self._quality != quality:
            self.close()
            from arduino.app_peripherals.camera import Camera  # lazy: only on the board
            self._cam = Camera(self.source, resolution=(w, h), fps=fps)
            self._cam.start()
            self._quality = quality
        return w, h

    @staticmethod
    def _encode_jpeg(frame) -> bytes:
        """RGB ndarray -> JPEG bytes, SDK-faithful. Prefer the SDK's compress_to_jpeg."""
        try:
            from arduino.app_utils.image.adjustments import compress_to_jpeg  # SDK path
            buf = compress_to_jpeg(frame)
            if buf is None:
                raise OSError("compress_to_jpeg returned None")
            return buf.tobytes()
        except ImportError:
            import cv2  # fallback: convert RGB->BGR so colours are correct
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(".jpg", bgr)
            if not ok:
                raise OSError("jpeg encode failed")
            return buf.tobytes()

    def capture(self, quality: str) -> Frame:
        self._ensure(quality)
        frame = self._cam.capture()
        if frame is None:
            raise OSError("arduino camera returned no frame")
        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        return Frame(data=self._encode_jpeg(frame), fmt="jpeg", width=fw, height=fh)

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
            except Exception:  # noqa: BLE001 - best effort
                pass
            self._cam = None


def detect_arduino_camera(source=0) -> "ArduinoCamera | None":
    """Return an Arduino App Bricks camera if the SDK is present AND a frame can be captured."""
    try:
        import arduino.app_peripherals.camera  # noqa: F401
    except Exception:  # noqa: BLE001 - SDK absent (e.g. on a Mac)
        return None
    cam = ArduinoCamera(source)
    try:
        cam.capture("low")  # prove a real frame comes back
        return cam
    except Exception:  # noqa: BLE001 - no camera plugged / capture failed
        cam.close()
        return None


def detect_v4l2_camera(device: str = "/dev/video0") -> "V4l2Camera | None":
    """Return a real camera if a video device AND a capture tool are present, else None."""
    import os
    import shutil
    if not os.path.exists(device):
        return None
    if not (shutil.which("ffmpeg") or shutil.which("fswebcam")):
        return None
    return V4l2Camera(device)


def probe_environment() -> dict:
    """Report what the host actually offers for camera capture. Logged at boot so the FIRST
    remote deploy answers the open questions (does the App Bricks SDK import here? is cv2
    present? which /dev/video* and /dev/v4l/by-id devices exist?) without a separate app."""
    import glob
    import os

    info: dict = {}
    try:
        info["uname"] = " ".join(os.uname())
    except Exception:  # noqa: BLE001
        info["uname"] = "?"
    info["dev_video"] = sorted(glob.glob("/dev/video*"))
    info["v4l_by_id"] = sorted(glob.glob("/dev/v4l/by-id/*"))

    def _importable(mod: str) -> bool:
        import importlib.util
        try:
            return importlib.util.find_spec(mod) is not None
        except Exception:  # noqa: BLE001
            return False

    info["has_arduino_sdk"] = _importable("arduino.app_peripherals.camera")
    info["has_cv2"] = _importable("cv2")
    info["has_numpy"] = _importable("numpy")

    # What list_devices() (the SDK's own USB scan) sees, if the SDK is present.
    info["sdk_usb_devices"] = None
    if info["has_arduino_sdk"]:
        try:
            from arduino.app_peripherals.camera import V4LCamera
            info["sdk_usb_devices"] = V4LCamera.list_devices()
        except Exception as e:  # noqa: BLE001
            info["sdk_usb_devices"] = f"error: {e}"
    return info


SOURCE_MODES = ("auto", "mock", "real")


def select_camera(mode: str = "auto", device: str = "/dev/video0") -> Camera:
    """Pick the camera by source mode — switchable on demand (startup env or the live API):

    - ``auto``  : the Arduino App Bricks camera on a UNO Q, else a generic V4L2 device, else mock.
    - ``mock``  : synthetic PNG frames (an end-to-end test can run with no camera).
    - ``real`` / ``arduino`` : the Arduino UNO Q camera (deltaco USB via the App Bricks SDK),
      falling back to a generic V4L2 device; raises if no camera. The SDK-faithful path.
    - ``v4l2``  : a generic /dev/video* camera via ffmpeg/fswebcam (non-UNO-Q hosts).
    """
    mode = (mode or "auto").lower()
    if mode == "mock":
        return MockCamera()

    if mode in ("auto", "real", "arduino"):
        arduino = detect_arduino_camera()
        if arduino is not None:
            return arduino

    v4l2 = detect_v4l2_camera(device)
    if v4l2 is not None:
        return v4l2

    if mode in ("real", "arduino", "v4l2"):
        raise OSError("no Arduino/UNO-Q camera or V4L2 device available")
    return MockCamera()
