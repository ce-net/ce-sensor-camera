#!/usr/bin/env python3
"""ce-sensor-camera runtime — a cap-gated camera producer with a control API.

Wires the hardware-agnostic :class:`CameraService` to the local CE node via the shared
``ce`` client:
- an ANNOUNCE loop publishes "this camera exists" on a well-known topic (discovery by
  name, never by address);
- a SERVE loop streams inbound control requests into ``service.handle`` (cap-gated
  status/start/stop/set_quality/take_image/subscribe);
- a PUSH loop feeds ``service.tick`` and sends each frame to cleared subscribers while
  streaming.

Config (all optional, env-driven — no flags, no addresses):
- ``CE_SENSOR_INSTANCE``  a name for this camera (e.g. ``cam-entrance``).
- ``CE_SENSOR_INTERVAL``  seconds between streamed frames (default 1).
- ``CE_SENSOR_QUALITY``   ``low`` | ``medium`` (default) | ``high``.
- ``CE_SENSOR_STREAM``    ``1`` to start streaming at boot (default off; start via the control API).
- ``CE_SENSOR_AUTH``      ``capiam`` (default, real ce-iam verify) | ``allowlist`` | ``allow`` | ``deny``.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import ce

from capauth import authorizer_from_env
from camera.service import (
    ANNOUNCE_TOPIC,
    CTL_TOPIC,
    DATA_TOPIC,
    SERVICE,
    CameraService,
)
from camera.source import DEFAULT_QUALITY, select_camera

log = logging.getLogger("ce-sensor-camera")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = ce.connect().wait_ready()
    node_id = client.node_id
    instance = os.environ.get("CE_SENSOR_INSTANCE", "camera")
    interval = float(os.environ.get("CE_SENSOR_INTERVAL", "1"))
    quality = os.environ.get("CE_SENSOR_QUALITY", DEFAULT_QUALITY)
    authorizer = authorizer_from_env()

    # Auto-select: a real /dev/video* camera if present (via ffmpeg/fswebcam), else mock.
    # Force with CE_SENSOR_CAMERA=mock|real|auto and CE_SENSOR_DEVICE=/dev/videoN; switch live
    # via the `set_source` control op.
    device = os.environ.get("CE_SENSOR_DEVICE", "/dev/video0")
    source = os.environ.get("CE_SENSOR_CAMERA", "auto")
    selector = lambda mode: select_camera(mode, device)  # noqa: E731 - tiny closure over device
    camera = selector(source)
    service = CameraService(camera, authorizer, node_id, instance, interval=interval,
                            quality=quality, selector=selector, source=source)
    if os.environ.get("CE_SENSOR_STREAM") == "1":
        service.streaming = True
    log.info("%s (%s) up on node %s; quality=%s interval=%ss streaming=%s",
             SERVICE, instance, node_id[:16], service.quality, interval, service.streaming)

    def announce_loop() -> None:
        while True:
            try:
                client.publish(ANNOUNCE_TOPIC, service.announce_payload())
            except ce.CeError as e:
                log.warning("announce failed: %s", e)
            time.sleep(max(interval * 2, 2.0))

    def push_loop() -> None:
        while True:
            for to, payload in service.tick():
                try:
                    client.send(to, DATA_TOPIC, payload)
                except ce.CeError as e:
                    log.warning("frame push to %s failed: %s", to[:12], e)
            time.sleep(interval)

    threading.Thread(target=announce_loop, name="announce", daemon=True).start()
    threading.Thread(target=push_loop, name="push", daemon=True).start()

    # Blocks forever, serving cap-gated control requests. The supervisor restarts on exit.
    client.serve([CTL_TOPIC], service.handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
