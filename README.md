# ce-sensor-camera

A modular, cap-scoped **camera** sensor for the CE mesh, written as a Python `script`-tier
ceapp. It provides a continuous frame stream plus a control API other systems drive —
start/stop the stream, raise/lower quality, and grab a one-shot image. Add another camera by
running one install; consumers pick it up by topic and never change.

Part of the building-telemetry mesh. Sibling: the ce-sensor-climate ceapp
(github.com/ce-net/ce-sensor-climate). Uses the shared Python client ce-py
(github.com/ce-net/ce-py; `ce.py` is vendored here for the zero-install script tier).

## How it works

- **Discovery, no address.** Announces itself on `ce.sensor/announce` — a consumer learns
  it exists (service, node id, control topic, required capabilities, quality ladder) by
  listening, never by an IP address.
- **Access, by capability.** A consumer presents a capability on `ce.sensor/camera/ctl`.
  The sensor verifies it (via `ce-iam verify`, fail-closed, rooted at the building-org root)
  before serving. Two levels:
  - `building:camera:read` — `status`, `take_image`, `subscribe` (see the camera).
  - `building:camera:control` — `start`, `stop`, `set_quality` (change what it does).
- **Stream.** While streaming, frames are pushed to cleared subscribers on
  `ce.sensor/camera/frame`. Leases expire (default 60s); re-subscribe to keep them alive.

### Control protocol (`ce.sensor/camera/ctl`, request/reply, JSON)

Every request carries `{"cap": "<token>"}`.

| op | level | effect |
|---|---|---|
| `status` | read | streaming?, quality, subscriber count, quality ladder |
| `subscribe` / `unsubscribe` | read | start/stop receiving the frame stream |
| `take_image` | read | capture one frame now, returned in the reply (works even when stopped) |
| `start` / `stop` | control | turn the continuous stream on/off |
| `set_quality` | control | `{"quality":"low"\|"medium"\|"high"}` |

### Frame schema (`ce.sensor.frame/1`)

Identity block + the encoded image inline as `bytes_hex` (a valid PNG). Inline keeps the
stream reliable across nodes today; for real high-resolution video the same schema swaps in
a blob `cid`, with no consumer change.

## Real hardware (plug and play)

`camera/source.py` is the only hardware-specific file. Mock PNG frames ship by default (no
Pillow/OpenCV — generated with stdlib `zlib`); to use a real camera on a UNO Q, implement
`V4l2Camera.capture()` against `/dev/video0` and swap it in `main.py`. Nothing else changes.

## Configuration (env, no flags)

| Var | Default | Meaning |
|---|---|---|
| `CE_SENSOR_INSTANCE` | `camera` | Name for this camera (e.g. `cam-entrance`). |
| `CE_SENSOR_INTERVAL` | `1` | Seconds between streamed frames. |
| `CE_SENSOR_QUALITY` | `medium` | `low` / `medium` / `high`. |
| `CE_SENSOR_STREAM` | – | `1` to start streaming at boot (default: start via the control API). |
| `CE_SENSOR_AUTH` | `capiam` | `capiam` (real `ce-iam verify`) / `allowlist` / `allow` (dev) / `deny`. |
| `CE_SENSOR_ALLOW` | – | Comma-separated NodeIds for `allowlist` mode. |

## Develop & test

```bash
pytest    # cap-gating (read vs control), control ops, stream tick, PNG validity, schema
```

No node, no ce-iam, no hardware — the service logic is pure (`handle`/`tick`).

## Deploy

```bash
ce app install ./ce-sensor-camera --on node=<camera-board>
```
