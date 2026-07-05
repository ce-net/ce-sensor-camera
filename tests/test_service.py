"""Unit tests for CameraService: two-level cap-gating, the control API, and the frame tick.

Pure: handle/tick take a Message and return data, so no live node, ce-iam, or hardware.
"""

from __future__ import annotations

import json

from ce import Message

from capauth import AllowAll, Authorizer, DenyAll
from camera.frame import decode_frame, frame_bytes
from camera.service import ACTION_CONTROL, ACTION_READ, CameraService
from camera.source import MockCamera

CAM_NODE = "aa" * 32
CONSUMER = "bb" * 32


class ReadOnly:
    """Grants read-level actions but denies control-level ones (tests the split)."""

    def authorize(self, cap: str, action: str, requester: str, on_node: str) -> bool:
        return action == ACTION_READ


def _svc(authorizer: Authorizer, now=None, **kw) -> CameraService:
    return CameraService(MockCamera(), authorizer, CAM_NODE, "test",
                         interval=1.0, lease=60.0, now=now or (lambda: 1000.0), **kw)


def _req(payload: bytes, token: int = 1) -> Message:
    return Message(sender=CONSUMER, topic="ce.sensor/camera/ctl", payload=payload,
                   reply_token=token)


def test_status_requires_read_capability():
    assert b"unauthorized" in _svc(DenyAll()).handle(_req(b'{"op":"status"}'))


def test_start_requires_control_capability_not_just_read():
    svc = _svc(ReadOnly())
    reply = svc.handle(_req(b'{"op":"start","cap":"x"}'))
    assert b"unauthorized" in reply and ACTION_CONTROL.encode() in reply
    assert svc.streaming is False


def test_start_stop_with_control_capability():
    svc = _svc(AllowAll())
    assert json.loads(svc.handle(_req(b'{"op":"start","cap":"x"}')))["streaming"] is True
    assert svc.streaming is True
    assert json.loads(svc.handle(_req(b'{"op":"stop","cap":"x"}')))["streaming"] is False
    assert svc.streaming is False


def test_set_quality_validates_and_applies():
    svc = _svc(AllowAll())
    assert json.loads(svc.handle(_req(b'{"op":"set_quality","quality":"high","cap":"x"}')))["quality"] == "high"
    assert svc.quality == "high"
    assert b"unknown quality" in svc.handle(_req(b'{"op":"set_quality","quality":"ultra","cap":"x"}'))


def test_take_image_is_read_level_and_returns_a_frame():
    # ReadOnly may not control, but take_image is read-level and must work.
    frame = decode_frame(_svc(ReadOnly()).handle(_req(b'{"op":"take_image","cap":"x"}')))
    assert frame["sensor"] == "ce-sensor-camera"
    assert frame["format"] == "png"
    assert frame_bytes(frame)[:8] == b"\x89PNG\r\n\x1a\n"


def test_take_image_denied_without_capability():
    assert b"unauthorized" in _svc(DenyAll()).handle(_req(b'{"op":"take_image"}'))


def test_stream_only_pushes_when_started_and_subscribed():
    svc = _svc(AllowAll())
    # subscribe but not streaming -> no frames
    svc.handle(_req(b'{"op":"subscribe","cap":"x"}'))
    assert svc.tick() == []
    # start streaming -> frames pushed to the cleared subscriber
    svc.handle(_req(b'{"op":"start","cap":"x"}'))
    sends = svc.tick()
    assert len(sends) == 1
    to, payload = sends[0]
    assert to == CONSUMER
    assert decode_frame(payload)["seq"] == 0
    # seq advances each tick
    assert decode_frame(svc.tick()[0][1])["seq"] == 1


def test_streaming_with_no_subscribers_pushes_nothing():
    svc = _svc(AllowAll())
    svc.handle(_req(b'{"op":"start","cap":"x"}'))
    assert svc.tick() == []


def test_subscription_lease_expires():
    t = {"v": 1000.0}
    svc = _svc(AllowAll(), now=lambda: t["v"])
    svc.handle(_req(b'{"op":"subscribe","cap":"x"}'))
    svc.handle(_req(b'{"op":"start","cap":"x"}'))
    assert len(svc.tick()) == 1
    t["v"] += 61.0
    assert svc.tick() == []
    assert svc.subscribers == {}


def test_unauthorized_subscriber_never_receives_frames():
    svc = _svc(DenyAll())
    svc.streaming = True
    svc.handle(_req(b'{"op":"subscribe"}'))
    assert svc.tick() == []


def test_announce_advertises_both_capability_levels_by_name():
    ann = json.loads(_svc(AllowAll()).announce_payload())
    assert ann["service"] == "ce-sensor-camera"
    assert ann["action_read"] == ACTION_READ
    assert ann["action_control"] == ACTION_CONTROL
    assert "high" in ann["qualities"]
    assert "ip" not in ann and "addr" not in ann


def test_unknown_op_and_bad_json():
    svc = _svc(AllowAll())
    assert b"unknown op" in svc.handle(_req(b'{"op":"nope","cap":"x"}'))
    assert b"bad request" in svc.handle(_req(b"not json"))


def test_set_source_is_control_level():
    # ReadOnly may take_image but not switch the source (control-level).
    svc = CameraService(MockCamera(), ReadOnly(), CAM_NODE, "test",
                        selector=lambda m: MockCamera(), source="auto")
    assert b"unauthorized" in svc.handle(_req(b'{"op":"set_source","source":"mock","cap":"x"}'))


def test_set_source_switches_camera_on_demand():
    calls = []

    class Marker(MockCamera):
        pass

    def selector(mode):
        calls.append(mode)
        return Marker()

    svc = CameraService(MockCamera(), AllowAll(), CAM_NODE, "test",
                        selector=selector, source="auto")
    reply = json.loads(svc.handle(_req(b'{"op":"set_source","source":"real","cap":"x"}')))
    assert reply["source"] == "real" and calls == ["real"]
    assert svc.source_mode == "real" and isinstance(svc.camera, Marker)
    status = json.loads(svc.handle(_req(b'{"op":"status","cap":"x"}')))
    assert status["source"] == "real" and status["camera"] == "Marker"
