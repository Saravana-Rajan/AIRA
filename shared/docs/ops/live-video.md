# Live video on the dashboard: WebRTC with a JPEG fallback

The dashboard's Live page shows what Sam's chest camera sees. Two paths feed it:

| Path | What travels | Rate | Latency | Used when |
|---|---|---|---|---|
| **WebRTC** (peer to peer, phone -> dashboard) | one video track, sendonly, ~640x360, <= 24 fps, <= 600 kbps, `degradationPreference: maintain-framerate` | 15-30 fps | ~150 ms | the peers can connect (STUN reaches both) |
| **JPEG preview** (phone -> Cloud Run -> dashboard over the WebSockets) | `frame` messages with `purpose: "preview"`, <= 480 px, quality 0.5 | 2-3 fps | ~0.5-1 s | always, as the automatic fallback |

Nothing of either path reaches Gemini: preview frames are relayed to the monitors only, and the WebRTC media
never touches the server at all.

## Signalling

Our WebSockets carry only the signalling, as one message shape on every channel
(`shared/contracts/src/messages.ts`, `backend/aira_live/contracts.py`):

```
{ t: "rtc", kind: "want" | "offer" | "answer" | "ice" | "connected" | "bye", from, to, sdp?, candidate? }
```

- `from` is always the sender's id **as the server knows it** (a phone's session id, or the monitor id the
  server gave the dashboard); whatever a client puts there is overwritten. `to` names the peer.
- The server (`backend/aira_live/rtc.py`) relays only between a monitor and a phone session **of the same
  shop**. There is no monitor key any more (removed by the owner's decision; see `security.md` §2a): a monitor
  is any socket on `/monitor` whose Origin passed the allowlist. It never reads the SDP: offers and answers are capped at
  64 KB, candidates at 1 KB, each socket at 40 signalling messages a second, and `diag("rtc", ...)` logs the
  kind, the ids and the size only.

The flow:

1. The dashboard follows a session and sends `{t:"rtc", kind:"want", to:<session id>}` on `/monitor`
   (`frontend/dashboard/src/lib/rtcViewer.ts`).
2. The phone (`frontend/app/src/camera/rtcSender.ts`) opens an `RTCPeerConnection`, adds the camera track it
   **already** has (the same `MediaStream` the frames come from: no second camera prompt), video only, and
   sends the `offer`.
3. The dashboard answers `recvonly`; both sides trickle `ice`. ICE uses `stun:stun.l.google.com:19302` only.
4. When the connection reaches `connected`, the phone says so; the server then stops asking that session for
   JPEG preview frames (`request_frame preview stop`) **as long as every monitor of the shop has video**, which
   saves the phone's data.
5. On `bye` from either side, a monitor leaving, or a session ending, the preview resumes for the monitors
   still watching.

## Fallback

The dashboard keeps drawing JPEG frames until WebRTC is `connected`. If that does not happen within 6 s, or
the connection fails, disconnects or the phone says `bye`, it sends `bye`, closes the connection, keeps the
JPEG preview and asks again 15 s later. The camera card carries a small badge: **WebRTC** or **Preview**.

Behind a strict NAT (symmetric NAT on the phone's mobile network, a corporate firewall on the laptop) the
peers never connect and the Live page simply stays on the JPEG preview. Adding a TURN server is optional
later: put its `urls`/`username`/`credential` into `ICE_SERVERS` in both `rtcSender.ts` and `rtcViewer.ts`.

## Privacy and cleanup

- Video only, never an audio track.
- The peer connection is closed (with `bye`) when the monitor leaves the Live page or hides the tab, when
  the phone's page hides, when the camera wiring is disposed, and when the session ends.
- The server holds no media and no SDP: only "which monitor is connected to which session", in memory.
