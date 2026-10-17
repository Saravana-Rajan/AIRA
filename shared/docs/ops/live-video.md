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
  shop**. A monitor is a socket on `/monitor` (when `AIRA_MONITOR_AUTH=1`, one that authenticated with a Firebase
  ID token for a Google account on `AIRA_MONITOR_VIEWERS`, and no signalling is relayed before that; by default
  no sign-in, see `security.md` §2a). It never reads the SDP: offers and answers are capped at
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

## Live audio: hear the session on the laptop (`AIRA_MONITOR_AUDIO=1`)

Audio does not ride on WebRTC. When the server runs with `AIRA_MONITOR_AUDIO=1` (its `ready` line on `/monitor`
says `audio: true`; otherwise the dashboard shows no button), the Live page's **Listen** button sends
`{t:"listen", session: <followed session id>}` on the same `/monitor` socket and the server relays that
session's sound as `{t:"mon", kind:"audio", session, ts, who, rate, pcm}`: `who: "owner"` is the phone's
microphone exactly as the phone sends it to Gemini (`rate: 16000`), `who: "aira"` is AIRA's voice exactly as
Gemini Live produced it on its way to the phone (`rate: 24000`), both mono PCM16 little-endian base64. The
request follows the person the page follows (a switch re-sends it), `session: null` stops it, and the relay
also stops when the monitor leaves or the session ends. Nothing is stored or transcoded on the server.

The dashboard (`frontend/dashboard/src/lib/listen.ts`) plays both voices on one `AudioContext` started by the
button's click (browsers need the gesture), one gain node per voice, each chunk an `AudioBufferSourceNode` at
the stream's own sample rate scheduled back to back on the context clock. The first chunk after silence starts
0.2 s ahead (the jitter buffer); a chunk that would start more than 0.6 s ahead is dropped so a stalled socket
never turns into a growing delay. A server-side queue of 32 chunks per monitor drops the oldest when a socket is
backed up (the count is in the monitor's close log line).

Bandwidth, **computed from the formats, not measured**: the microphone is 16 000 samples/s x 2 bytes = 32 kB/s raw,
about 43 kB/s as base64 (~340 kbit/s) while the phone streams; AIRA's voice is 24 000 x 2 = 48 kB/s raw, about
64 kB/s as base64 (~510 kbit/s) while she speaks. Both come on top of the preview frames or the WebRTC video.

Known limits: a laptop in the same room as the phone plays the owner's microphone back into it (echo, and AIRA
may hear herself): listen with headphones, or from another room. There is no echo cancellation and no
recording.

## Privacy and cleanup

- Video only on the WebRTC track, never audio; the audio relay above is a separate, opt-in path on `/monitor`
  (`AIRA_MONITOR_AUDIO=1`, see `security.md` §2a for who can hear it).
- The peer connection is closed (with `bye`) when the monitor leaves the Live page or hides the tab, when
  the phone's page hides, when the camera wiring is disposed, and when the session ends.
- The server holds no media and no SDP: only "which monitor is connected to which session", in memory.
