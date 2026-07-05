"""ce-sensor-camera — a modular, cap-gated camera sensor ceapp for the CE mesh."""

from .frame import FRAME_SCHEMA, decode_frame, encode_frame, frame_bytes
from .service import CameraService
from .source import (
    QUALITY_LADDER,
    Camera,
    Frame,
    MockCamera,
    V4l2Camera,
    detect_v4l2_camera,
    select_camera,
)

__all__ = [
    "CameraService",
    "Camera",
    "MockCamera",
    "V4l2Camera",
    "detect_v4l2_camera",
    "select_camera",
    "Frame",
    "QUALITY_LADDER",
    "FRAME_SCHEMA",
    "encode_frame",
    "decode_frame",
    "frame_bytes",
]
