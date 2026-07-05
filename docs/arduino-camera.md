# Arduino UNO Q camera — exact API (from the Arduino App Bricks SDK)

The camera on the Arduino UNO Q (a **deltaco USB camera**) is driven the Arduino way, via the
**App Bricks Python SDK** `arduino.app_peripherals.camera.Camera` — NOT raw ffmpeg. Sources:
- https://github.com/arduino/app-bricks-py — `src/arduino/app_peripherals/camera/`
- https://github.com/arduino/app-bricks-examples — the example apps for the UNO Q board
- Arduino App Lab docs (the UNO Q "Bricks"/peripherals framework).

## Leif's words — verbatim (2026-07-05)

> https://github.com/arduino/app-bricks-py   https://github.com/arduino/app-bricks-examples —
> These examples contain information on how to run the camera module properly — the deltaco camera.
> Use actual Arduino documentation — latest — for this exact thing. Use the repositories above to
> know and help, together with the latest docs from sources for this specific card. Document my
> words verbatim.

## The API (exactly from the SDK)

```python
from arduino.app_peripherals.camera import Camera

# Default camera = first available plugged camera (V4L/USB — this is the deltaco USB cam):
camera = Camera()

# Or a specific device index + configuration (kwargs forwarded to the implementation):
camera = Camera(0, resolution=(640, 480), fps=15)

camera.start()
image = camera.capture()   # -> numpy.ndarray (a BGR frame), or None
camera.stop()

# Context-manager form (auto start/stop):
with Camera(source, **options) as camera:
    frame = camera.capture()
    # frame.shape -> (height, width, channels)
```

- **Universal**: one API over CSI, USB (V4L), IP (rtsp/http/mjpeg), and WebSocket cameras; the
  type is auto-detected from `source`. Classes: `Camera`, `V4LCamera`, `CSICamera`, `IPCamera`,
  `WebSocketCamera`, `BaseCamera`. Errors: `CameraError`, `CameraOpenError`, `CameraReadError`, …
- **`capture()` returns a numpy `ndarray`** (BGR, cv2 convention). Encode to JPEG with
  `cv2.imencode(".jpg", frame)` (cv2 ships in the App Bricks runtime).
- Thread-safe; supports frame `adjustments` (e.g. `greyscaled`, cv2 pipelines via `|`).
- Deltaco USB camera: a commercial USB webcam → the default V4L camera. A USB-C hub with USB-A is
  the recommended way to attach it to the board.

## How this app uses it

`camera/source.py` `ArduinoCamera` wraps `arduino.app_peripherals.camera.Camera`: `start()` at the
selected quality (QUALITY_LADDER → resolution/fps), `capture()` → numpy → JPEG bytes. `select_camera`
`auto`/`real` picks it first (falls back to a generic V4L2 device, then mock). The SDK + cv2 are
imported lazily, so the app still loads and its tests run on a Mac (which has neither) — there it
uses mock, and the real deltaco camera is plug-and-play on the UNO Q. Switch live with the
`set_source auto|mock|real` control op.
