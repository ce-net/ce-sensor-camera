"""CameraService — a cap-gated camera producer with a control API, over the CE mesh.

Exposes exactly what Leif asked for: a continuous stream other systems can start/stop and
whose quality they can raise/lower, plus a one-shot take-image. There is no address and no
HTTP: a consumer discovers the camera by name (announce), presents a capability, and — if it
grants the required action rooted at the building-org root — controls it or receives frames.

Two capability levels:
- ``building:camera:read``    — status, take_image, subscribe (see the camera).
- ``building:camera:control`` — start, stop, set_quality (change what it does).

The logic is pure and testable (no threads, no live node, no hardware):
- :meth:`handle` — dispatch one control request, return the reply bytes.
- :meth:`tick`   — while streaming, capture one frame and return the directed sends to
  cleared subscribers.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Optional

from ce import Message

from capauth import Authorizer
from .frame import encode_frame
from .source import DEFAULT_QUALITY, QUALITY_LADDER, Camera

SERVICE = "ce-sensor-camera"
CTL_TOPIC = "ce.sensor/camera/ctl"
DATA_TOPIC = "ce.sensor/camera/frame"
ANNOUNCE_TOPIC = "ce.sensor/announce"
ACTION_READ = "building:camera:read"
ACTION_CONTROL = "building:camera:control"

DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_INTERVAL_SECONDS = 1.0


def _err(message: str) -> bytes:
    return json.dumps({"error": message}).encode("utf-8")


def _ok(**fields: object) -> bytes:
    body = {"ok": True}
    body.update(fields)
    return json.dumps(body).encode("utf-8")


class CameraService:
    def __init__(self, camera: Camera, authorizer: Authorizer, node_id: str,
                 instance: str = "camera", *, interval: float = DEFAULT_INTERVAL_SECONDS,
                 lease: float = DEFAULT_LEASE_SECONDS, quality: str = DEFAULT_QUALITY,
                 selector: Optional[Callable[[str], Camera]] = None, source: str = "auto",
                 now: Callable[[], float] = time.time) -> None:
        self.camera = camera
        self.authorizer = authorizer
        self.node_id = node_id
        self.instance = instance
        self.interval = interval
        self.lease = lease
        self.quality = quality if quality in QUALITY_LADDER else DEFAULT_QUALITY
        self.streaming = False
        self.seq = 0
        # `selector(mode) -> Camera` enables on-demand mock/real switching via the API; when
        # None the source is fixed to the constructed camera (e.g. in unit tests).
        self.selector = selector
        self.source_mode = source
        self._now = now
        self.subscribers: dict[str, float] = {}

    # ----- discovery -----

    def announce_payload(self) -> bytes:
        return json.dumps({
            "schema": "ce.sensor.announce/1",
            "service": SERVICE,
            "kind": "camera",
            "node": self.node_id,
            "instance": self.instance,
            "ctl_topic": CTL_TOPIC,
            "data_topic": DATA_TOPIC,
            "action_read": ACTION_READ,
            "action_control": ACTION_CONTROL,
            "qualities": list(QUALITY_LADDER.keys()),
        }, separators=(",", ":")).encode("utf-8")

    # ----- frame capture -----

    def capture_frame(self) -> bytes:
        frame = self.camera.capture(self.quality)
        payload = encode_frame(self.node_id, self.instance, self.seq, self._now(),
                               self.quality, frame)
        self.seq += 1
        return payload

    # ----- control plane (cap-gated) -----

    def handle(self, msg: Message) -> Optional[bytes]:
        try:
            req = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _err("bad request: expected JSON")
        if not isinstance(req, dict):
            return _err("bad request: expected object")
        op = req.get("op")
        cap = req.get("cap", "")

        # Control ops mutate what the camera does; they need the higher capability.
        control_ops = {"start", "stop", "set_quality", "set_source"}
        action = ACTION_CONTROL if op in control_ops else ACTION_READ
        if not self.authorizer.authorize(cap, action, msg.sender, self.node_id):
            return _err(f"unauthorized: present a capability granting {action}")

        if op == "status":
            return _ok(service=SERVICE, instance=self.instance, streaming=self.streaming,
                       quality=self.quality, interval=self.interval,
                       subscribers=len(self._live()), qualities=list(QUALITY_LADDER.keys()),
                       source=self.source_mode, camera=type(self.camera).__name__)
        if op == "set_source":
            return self._set_source(req.get("source"))
        if op == "start":
            self.streaming = True
            return _ok(streaming=True)
        if op == "stop":
            self.streaming = False
            return _ok(streaming=False)
        if op == "set_quality":
            q = req.get("quality")
            if q not in QUALITY_LADDER:
                return _err(f"unknown quality {q!r}; choose one of {list(QUALITY_LADDER)}")
            self.quality = q
            return _ok(quality=q)
        if op == "take_image":
            return self.capture_frame()
        if op == "subscribe":
            self.subscribers[msg.sender] = self._now() + self.lease
            return _ok(subscribed=True, data_topic=DATA_TOPIC, streaming=self.streaming)
        if op == "unsubscribe":
            self.subscribers.pop(msg.sender, None)
            return _ok(subscribed=False)
        return _err(f"unknown op: {op!r}")

    def _set_source(self, source) -> bytes:
        """Switch the camera source on demand (auto/mock/real) so end-to-end tests can force
        mock and real hardware stays plug-and-play."""
        if self.selector is None:
            return _err("source switching not available on this instance")
        mode = str(source or "").lower()
        if mode not in ("auto", "mock", "real"):
            return _err("source must be one of auto|mock|real")
        try:
            self.camera = self.selector(mode)
        except OSError as e:
            return _err(f"cannot switch to {mode}: {e}")
        self.source_mode = mode
        return _ok(source=mode, camera=type(self.camera).__name__)

    # ----- data plane (push frames to cleared subscribers) -----

    def tick(self) -> list[tuple[str, bytes]]:
        """While streaming, capture one frame and return the (subscriber, frame) sends due
        now. Expired leases are pruned. Returns nothing when stopped or with no subscribers."""
        if not self.streaming:
            self._live()  # still prune expired leases while idle
            return []
        live = self._live()
        if not live:
            return []
        payload = self.capture_frame()
        return [(node_id, payload) for node_id in live]

    def _live(self) -> list[str]:
        now = self._now()
        for n in [n for n, exp in self.subscribers.items() if exp <= now]:
            del self.subscribers[n]
        return list(self.subscribers.keys())
