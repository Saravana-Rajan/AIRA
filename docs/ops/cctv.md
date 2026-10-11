# AIRA Shop Camera: CCTV / IP camera support (built, off by default)

The demo runs on the chest-mounted phone alone. This page is for the optional, configurable shop cameras in
`services/live/aira_live/cameras/`: fixed cameras that watch the shop and tell the owner, in his language, when
something needs him. Nothing in it runs until the `cameras` list in Firestore is non-empty.

## What it does

One watcher task per configured camera. Frames pass a cheap motion gate (grayscale 32x24, mean absolute
difference, at most `maxPerMinute` frames a minute) before anything reaches Gemini. Each camera runs one or more
modes:

| Mode | What it watches | What the owner hears | Gemini call |
|---|---|---|---|
| `delivery` | the delivery spot | "Camera front: a delivery seems to have come. I see about 12 packs. Shall I count them?" | ER 2 count |
| `shelf` | a shelf | "Camera shelf: only about 2 Aavin Milk 500 ml left on the shelf." (at or below `reorderThreshold`) | ER 2 count per product (first 4) |
| `counter` | the counter | "Camera front: someone is waiting at the counter." | ER 2 count |
| `security` | the shop after hours (default 22:00 to 06:00 IST) | "Camera front: movement in the shop after hours." | none |

Every notice is also an append-only `alerts` event (`shops/{shop}/events`, `kind: camera.<mode>`). A kind repeats at
most once per two minutes per camera. The language follows the owner's live session, else English.

The rules of the house apply. A camera never writes stock, money or state; a delivery it spots still goes through
the normal count and confirm flow. A count the model could not make says nothing. A saved shelf count is a hint,
not proof the packs are still there.

## Supported cameras

- **Any RTSP/ONVIF IP camera** (CP Plus, Hikvision, Dahua, TP-Link Tapo, most "CCTV" DVR/NVR boxes expose an RTSP
  URL per channel). The service runs `ffmpeg -rtsp_transport tcp -i <url> -vf fps=1/N,scale=768:-2` and reads MJPEG
  from its stdout; a dropped stream reconnects with backoff (1, 2, 4, 8, 16, 30 s). `ffmpeg` must be in the
  container image for this source; the phone source needs nothing.
- **A second phone** on a stand, opened at `https://<app>/camera` ("shop camera" mode). It sends one picture every
  5 s over the same WebSocket with the camera role (uid `camera:<name>`), no microphone, no Live session. The page
  is screen-reader friendly and light-themed like the rest of the app.

## Configuration

The `cameras` field of the Firestore doc `config/runtime` (database `aira`), read with the same 60 s cache as the
model switches. Default: absent or empty, so nothing runs.

```json
{
  "cameras": [
    { "name": "front", "source": "rtsp", "urlEnv": "CAMERA_FRONT_URL",
      "modes": ["delivery", "counter", "security"], "intervalS": 5, "enabled": true, "maxPerMinute": 6 },
    { "name": "shelf", "source": "phone", "modes": ["shelf"], "intervalS": 10, "enabled": true }
  ]
}
```

| Field | Meaning |
|---|---|
| `name` | lower-case `[a-z0-9_-]`, up to 32 chars; the phone page must use the same name |
| `source` | `rtsp` or `phone` |
| `urlEnv` | rtsp only: the env var that holds the RTSP URL (default `CAMERA_<NAME>_URL`) |
| `modes` | any of `delivery`, `shelf`, `counter`, `security` |
| `intervalS` | seconds between frames (1 to 600, default 5) |
| `enabled` | `false` switches one camera off without deleting it |
| `maxPerMinute` | frames that may reach Gemini per minute after the motion gate (default 6) |
| `afterHours` | `{"start": "22:00", "end": "06:00"}` for `security` (IST, wraps midnight) |

A bad entry is skipped with a warning; the good ones still run. Changing the list takes effect on the next service
start (the watchers are started in the app lifespan).

### Enabling it

1. Put the RTSP URL in Secret Manager and mount it as the env var named in `urlEnv` on the `aira-live` service.
   The URL carries the camera password: it is never written to Firestore, to the repo or to the logs (the ffmpeg
   command is logged with `<url>` in its place).
2. Add the `cameras` list to `config/runtime` in the Firestore console.
3. Redeploy or restart `aira-live`. The log line `shop cameras: watching front, shelf` confirms the watchers are up;
   `shop cameras: none configured (off)` is the default.
4. For a phone camera, open `/camera` on the second phone, type the camera name and tap **Start camera**.

To switch it off again, set `enabled: false` on each entry or remove the list, then restart.

## Privacy notice

A shop camera watches a public shop floor, and people walk through it. Keep the notice short and visible:
"This shop uses a camera that helps a blind shopkeeper. Pictures are analysed and not stored." The service keeps
no frames: a frame lives in memory until the next one and is sent to Gemini only when it changed. Events record the
spoken sentence, never an image. Point cameras at shelves, the counter and the delivery spot, not at the street.
Do not use `counter` mode to identify people; it only says that someone is there.

## Cost control

- The motion gate drops unchanged frames before any model call; a still shop costs nothing.
- `maxPerMinute` caps the model calls per camera (default 6 a minute, so at most 360 an hour per camera even
  in a busy shop). `intervalS` caps the decode work.
- `security` makes no model call at all. `shelf` makes one call per catalog product (first 4) per gated frame; give
  it a long `intervalS` (30 to 60 s).
- The phone source adds no Gemini Live session: a camera-role hello gets no Live bridge.
- Each notice kind repeats at most once per two minutes, so a crate left in view does not keep talking.

## Tests

`services/live/tests/cameras/`: config parsing (and off by default), the motion gate on generated frames, the
JPEG splitter and the RTSP source with a fake ffmpeg process (the one real-ffmpeg test skips when ffmpeg is
absent), the phone source, the watch loop's routing per mode, and the `main.py` lifespan hook.
`apps/app/test/camera-mode.test.tsx`: the `/camera` route, the camera hello, the streaming loop and the page.
