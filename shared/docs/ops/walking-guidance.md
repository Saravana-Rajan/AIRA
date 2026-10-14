# Walking guidance ("walk with me")

How AIRA guides the owner to a place in the shop by voice from the phone's chest camera, what the server
does on its own between his words, which watchdogs keep it honest, and what it is **not**. Flag: `AIRA_WALK=1`
(off by default). Code: `backend/aira_live/tools/walk.py` (the loop and the Live tools),
`backend/aira_live/vision/walk.py` (the policy), `backend/aira_live/nav_session.py` (the walk's own eyes),
`frontend/app/src/walk/*` (the on-device bump guard).

## 1. Architecture: the full path of one observation

```
phone                                   Cloud Run (aira_live)                              Gemini Live
-----                                   ---------------------                              -----------
camera 1 fps  --frame(purpose=live)-->  main.live_frame                                  
   |                                      |- session.frames.remember_recent                
   |                                      |- bridge.video.offer  --realtime video-------->  conversation session
   |                                      |- tools.walk.on_live_frame (when walking)         (voice + camera)
   |                                           |- nav.send_frame   --realtime video-------->  navigation session
   |                                           |- loop.on_live_frame (frame_at)               (camera only, mute)
   |                                           '- changed: loop.on_changed_frame -> check now
   |                                                                                        
   |   every CHECK_INTERVAL_S (2 s), one in flight:                                         
   |     WalkLoop.tick -> maybe_check -> NavChecker.request("[NAV CHECK n] ...")            
   |        nav session alive & ready  ----text---------------------------------------->  navigation session
   |        else conversation bridge.request_nav_check (only in a quiet moment) --------->  conversation session
   |                                                                                        
   |                                      nav_report(check_id=n, ...)  <--tool call--------  either session
   |                                        (NON_BLOCKING, response SILENT: never spoken)   
   |                                        '- _handle_report -> loop.on_report             
   |                                             '- vision.walk.parse_report / decide / gate / Speech
   |                                                 '- WalkLoop._choose (state machine)    
   |                                                      '- _say: TranscriptMsg + CueMsg("warn")
   |   <--transcript/cue-----------------                  '- bridge.speak_notice(urgent)  --[SYSTEM NOTE]-->  conversation session
   |   <--audio (AIRA's voice)-----------  bridge <--audio------------------------------------'
   |                                                                                        
MediaPipe EfficientDet-Lite0 (8 Hz, on device)                                              
   '- guard.ts: track -> time to contact / size -> 2 frames -> vibrate, warn earcon, speakUrgent("Stop")
   '- boxes (5/s) --boxes(source=mediapipe, severity=danger)-->  main -> tools.walk.on_phone_boxes -> fast stop
```

- **Perception is Gemini Live only.** No Robotics-ER call anywhere in a walk. The conversation session sees
  the camera as realtime video; with `AIRA_WALK_NAV_SESSION` (default on) a second, navigation-only Live
  session (`nav_session.py`) gets the same frames and all the `[NAV CHECK n]` notes, so checks keep flowing
  while the owner talks and AIRA answers. Measured on the real API (2026-10-14): a check sent into the
  conversation session while AIRA answered cut her answer (2/2); one in flight while the owner spoke was
  usually lost (3/4); the navigation session answered 29/29 checks (0.7-1.8 s) during conversation with no
  audio and no interruption. Its first ~3 answers can be empty (warm-up); a check sent with its very first
  frame came back empty, so it is `ready` only after `MIN_FRAMES = 2` frames.
- **One instruction per report, decided in code** (`vision.walk.decide`): hazards (steps, spill, cable...)
  and a large person in the path stop him on ONE report; a small or medium obstacle needs 2 of the last 3
  reports (Live's reports flicker); directions and the arrival candidate need the same key in 2 of the last 3
  reports AND a fresh observation (age <= `WALK_DIRECTION_MAX_AGE_S` = 3 s, measured when the line is
  said, so it includes the check's latency). Stops are said whatever the age.
- **Speech pacing** (`vision.walk.Speech`): the same non-urgent line again only after `DEDUPE_S` = 6 s
  (arrival candidate: 15 s), never two lines within 2 s unless the second is urgent, a repeated direction
  becomes "Keep going slowly." at most every 10 s. Urgent lines (every stop) are **never** suppressed: said
  again soon after, they take their short form ("Stop. Chair ahead."). The bridge's own notice dedupe is
  bypassed for walk lines (`_notices.pop` before `speak_notice`).
- **The voice path**: a walk line is a `[SYSTEM NOTE]` to the conversation session (`URGENT_PREFIX` for
  stops: Live drops the rest of its answer and cuts in), so Live says it in the language the owner is
  speaking. The English meaning goes to the phone as a caption (`TranscriptMsg`) with a `warn` earcon.
- **The phone's own safety layer** never waits for Gemini: `guard.ts` calls `app.speakUrgent` directly from
  the detector loop (vibration first, then the warn earcon, then the phrase), and `core/wire.ts speakUrgent`
  cuts Live audio, drops any waiting local line and speaks at once. Its `boxes` also reach the server, where
  `fast_stop_label` says "Stop. <label> in front." for a `danger` box or a person / large object low in the
  corridor (`CORRIDOR_X` 350-650, bottom >= 750, height >= 30 %), rate-limited per label (`FAST_STOP_GAP_S`
  = 3 s), never for the walk's own target.

## 2. State machine (`WalkLoop.state`, every transition logged as `walk_state`)

```
walk_to -> ACQUIRING --first confirmed direction--> NAVIGATING <----2 fresh usable reports---- HAZARD
              |                                      |  ^  \--obstacle / hazard / blocked / phone stop--> HAZARD
              |                                      |  |   \--target large + ahead (2 of 3)--> ARRIVAL_CANDIDATE
              |                                      v  |                                       (keeps checking)
              +--unusable view / 3 failed looks------> DEGRADED --2 fresh usable reports--> NAVIGATING
ACQUIRING / NAVIGATING / ARRIVAL_CANDIDATE --no observation for OBSERVATION_GAP_S--> DEGRADED
any state --walk_stop / long-press / disconnect / MAX_S / Live gone--> STOPPED (late reports ignored)
```

| From | To | Trigger | Said |
|---|---|---|---|
| any moving | HAZARD | hazard label in path (1 report); large person in path (1 report); small/medium obstacle in path (2 of 3); `path = blocked`; phone fast stop | "Sam, stop. Stairs ahead." / "...There's a chair ahead. The left side looks clearer in the current view..." / "Stop. Chair in front." (urgent, warn earcon) |
| HAZARD | NAVIGATING | 2 fresh usable reports with nothing in the path | "The chair is no longer in your path in the current view. Walk on slowly toward the fridge." |
| any moving | DEGRADED | report `view_ok=false` or `path=uncertain`; 3 failed looks in a row (timeout, empty after warm-up, no frame, no Live); **watchdog**: no observation for 10 s | "I can't see the path clearly right now. Please stop for a moment." (once per DEGRADED stretch) |
| DEGRADED | NAVIGATING | 2 fresh usable reports | "I can see the path again. Walk on slowly toward the fridge." |
| ACQUIRING / ARRIVAL_CANDIDATE | NAVIGATING | a confirmed direction | "The fridge is ahead. Keep going slowly." / "The fridge is on your left. Turn left a little." |
| NAVIGATING | ARRIVAL_CANDIDATE | target large and ahead in 2 of 3 fresh reports | "The fridge appears to be just ahead. Reach forward carefully to confirm." (never "you have arrived") |
| any | ACQUIRING | `walk_to` with a new target (same loop, check in flight ignored) | "Okay. Now I will guide you to the door. Walk slowly." (Live's own words from the tool result) |
| any | STOPPED | `walk_stop` (any stop / wait / "I'm here" intent, Live decides), long-press, phone disconnect | "Okay, stopped." (tool result) / nothing on a disconnect |
| any | STOPPED | `MAX_S` = 300 s | "I stopped guiding. Ask me again when you are ready." |
| any | STOPPED | the conversation bridge is closed for good (reconnects ran out) | "I stopped guiding: I lost the voice connection. Please stop and ask me again when you are ready." |

While stopped (HAZARD / DEGRADED) and silent for 15 s: "Stay stopped. I'm still checking the way." (every
15 s). While moving with observations but nothing worth a line for 20 s: "Still guiding you to the fridge."
(at most once a minute).

Scene change during a pending check: the phone marks a live frame `changed` while a check is in flight ->
that check's report describes an older view: it may still stop him, never direct him, and the new view is
checked at once (`walk_check reason=scene_changed`).

## 3. Timeouts and watchdogs

| Name | Value | Where | Effect |
|---|---|---|---|
| `TICK_S` | 0.5 s | `WalkLoop.run` | the loop's heartbeat |
| `CHECK_INTERVAL_S` | 2 s | `tick` | one `[NAV CHECK]` at most this often, one in flight |
| `CHANGED_CHECK_GAP_S` | 0.75 s | `on_changed_frame` | a changed frame checks at once if the last check is older than this |
| `CHECK_TIMEOUT_S` | 4 s | `tick` | a check without its report is a failed look |
| `MAX_FAILS` | 3 | `_failed` | failed looks in a row -> DEGRADED, honest line once |
| `NAV_WARMUP_CHECKS` | 3 | `on_report` | a new navigation session's first empty answers are not failed looks |
| preemption | - | `tick` | without a navigation session, a check in flight when the owner starts talking is forgotten (not failed); its late report is `stale` |
| `NAV_QUIET_S` | 0.8 s | `LiveBridge.request_nav_check` | a conversation-session check waits this long after the last words heard / answer end, and never goes while answering, while the owner speaks, during a long-press stop or while reconnecting |
| `OBSERVATION_GAP_S` | 10 s | `tick` | **watchdog**: moving with no observation this long, for any reason -> DEGRADED + honest line; recovery by 2 fresh usable reports |
| `NAV_REOPEN_S` / `NAV_MAX_OPENS` | 10 s / 4 | `_maybe_reopen_nav` | a navigation session that failed to open or dropped is reopened every 10 s, at most 4 opens per walk (`walk_nav` diag: open / open_failed / reopen); quiet-moment checks through the conversation in between |
| `OPEN_TIMEOUT_S` | 8 s | `NavSession.start` | opening the navigation session (in the background: the walk never waits) |
| `HOLD_SILENT_S` | 15 s | `tick` | "Stay stopped..." while HAZARD / DEGRADED and silent |
| `STILL_GUIDING_SILENT_S` / `_EVERY_S` | 20 s / 60 s | `tick` | "Still guiding..." while moving and silent |
| `FAST_STOP_GAP_S` | 3 s | `on_phone_boxes` | the same phone label stops him again only after this; a different label at once |
| `WALK_DIRECTION_MAX_AGE_S` | 3 s | `vision.walk.gate` | an older observation says stops only |
| `DEDUPE_S` / `MIN_GAP_S` / `REASSURE_S` / `ARRIVAL_REPEAT_S` | 6 / 2 / 10 / 15 s | `Speech` | pacing of non-urgent lines |
| `MAX_S` | 300 s | `run` | a walk ends by itself |
| `DISCONNECT_NOTE_S` | 60 s | `on_session_resumed` | a walk ended by a disconnect is reported to Live on a resume within this long (= `sessions.RESUME_WINDOW_S`) |
| `MEMORY_TIMEOUT_S` | 1.5 s | `remembered_place` | the saved "last seen" place; the walk never waits longer |
| phone `SERVER_WALK_MAX_MS` | 15 min | `camera/wire.ts` | a server walk whose stop message was lost stops counting as active |
| phone `BUMP_INTERVAL_MS` / `DETECT_TIMEOUT_MS` / `ERROR_BACKOFF_MS` | 125 / 1000 / 500 ms | `walk/guard.ts` | detector cadence; a hung detection or a camera error never stops the guard |
| phone `COOLDOWN_MS` / `GLOBAL_COOLDOWN_MS` / `CONFIRM_FRAMES` | 1.5 s / 5 s / 2 | `walk/bump.ts` | one warning per object per 1.5 s, one of any kind per 5 s, confirmed in 2 consecutive frames |
| phone `URGENT_DEDUPE_MS` / `URGENT_QUIET_MS` | 2 s / 0.9 s | `core/wire.ts` | the same urgent line within 2 s once (shorter than the guard's 5 s cooldown, so every guard warning is heard); Live chunks are held 0.9 s behind an urgent line, then played |

Network loss and recovery:

- **Phone socket drops**: `main._end_session` -> `tools.walk.stop_session` ends the walk (nobody sees him
  move), closes the navigation session and cancels its tasks; the phone's `ready` after a reconnect ends its
  own server-walk mode. The walk is **not** resumed by itself: `on_session_resumed` (needs the main.py hook,
  see §6) tells Live the walk ended so it tells the owner and calls `walk_to` again only when he asks.
- **Gemini Live socket drops (phone fine)**: the bridge reconnects with its resumption handle; meanwhile
  checks through the conversation are "busy" (not failures), the navigation session keeps answering, and walk
  lines are queued and replayed on reconnect (stale notices older than `NOTICE_DEDUPE_S` dropped). If the
  reconnects run out (`LiveBridge.closed`), the walk ends with the stop line above. If the outage keeps
  observations away for `OBSERVATION_GAP_S`, the watchdog stops him honestly first.
- **Navigation session drops**: `nav_session lost` diag, fall back to quiet-moment checks, reopen attempts as
  above; a check pending on the dead session times out (one failed look).

## 4. Flags and configuration

| Env | Default | Meaning |
|---|---|---|
| `AIRA_WALK` | `0` (off) | `1`: `walk_to` / `walk_stop` / `nav_report` are in the Live tool list and rule 18 of the prompt is the walking rule. Independent of `AIRA_ER2`. |
| `AIRA_WALK_NAV_SESSION` | `1` (on) | `0`: no navigation-only session; checks only in quiet moments of the conversation (the walk goes blind while he talks; the watchdog then stops him after 10 s). |
| `AIRA_OWNER_NAME` | unset | stops address him by name ("Sam, stop."); unset: "Stop.". |
| `AIRA_MOCK` / no `GEMINI_API_KEY` | mock when the key is missing | **mock isolation**: no Live bridge, no navigation session (`default_nav_factory` returns None and no reopen is tried), no checks are sent, no cloud call; a walk started in mock degrades to the honest caption after 3 looks. |
| `AIRA_LIVE_VIDEO` | `1` | must stay on: the walk's frames are the conversation session's realtime video (`main.live_frame`); off, no frame ever reaches a check (`fail.no_frame` -> DEGRADED). |
| `AIRA_ER2` | `1` | legacy: the Robotics-ER tools and ambient mode. Referenced in `config.py` (`Settings.er2`), `main.py` (tool list, ambient start, precount), `prompt.py` (`rules_for`, `RULES_LIVE_ONLY`), `tools/__init__.py` (`active_tools`), `nav_session.py` (`render_prompt(er2=False, walk=True)`), tests `test_er2_switch.py` / `test_walk.py`. Walking never uses ER; the flag only decides which other camera tools exist. |

Local (`backend/.env`): `GEMINI_API_KEY=<key>`, `AIRA_WALK=1`, `AIRA_WALK_NAV_SESSION=1`,
`AIRA_OWNER_NAME=Sam`, `AIRA_LIVE_VIDEO=1`; run `cd backend && uv run uvicorn aira_live.main:app --reload`,
`cd frontend && npm run dev -w app`. Production (Cloud Run, the AIRA project in asia-south1, id only in env
`GCP_PROJECT`): the same variables on the service (`AIRA_WALK=1` enables it; it is off until set), the API key
from Secret Manager. The phone needs nothing: the bump guard is always built in (`bumpGuard` defaults to true
in `wireCameraIntoApp`; `/try` passes false), the server walk is signalled by `request_frame purpose="walk"
continuous` / `stop`.

Diagnostics to watch (`aira` field in the JSON logs): `walk_start`, `walk_state`, `walk_check` (`sent`,
`reason`, `skipped=busy|preempted|timeout`, `deferred_ms`), `walk_tick` (`key`, `said`, `age_ms`, `fails`),
`walk_fast_stop`, `walk_nav` (`open` / `open_failed` / `reopen`, `attempt`), `nav_session` (`open` /
`open_failed` / `lost` / `closed` with frames and checks), `walk_end` (`why`), and on the phone `guard_alert`
(`kind`, `waitedMs`, `overLive`, `deduped`) and `guard_walk`.

## 5. Safety limitations: not a mobility aid

- **This is not a replacement for a cane, a guide dog or sighted assistance.** It is voice guidance from a
  1 fps chest camera interpreted by a language model, plus a generic object detector on the phone. It is
  for a shop floor the owner already knows, at walking pace, with his usual aid in hand.
- **No depth, no distances.** Nothing measures how far anything is. "Close" means the object looks large in
  the frame; "ahead / left / right" is the side in the current view only. No line ever gives steps or metres,
  and none says the way is "clear" or "safe" (`test_walk_speech_never_claims_distance_depth_or_a_safe_clear_way`).
- **Stairs, steps, drop-offs, wet floors** are reported only if Gemini names them in a `nav_report` (a
  `HAZARD` label pattern); the phone detector (COCO classes: person, chair, table, bench, couch, suitcase,
  bicycle, motorcycle, dog, cat, potted plant...) **cannot see them** and its phrases never claim to
  ("Stop", "Obstacle left / right"). A glass door, a low step, a hole or a wet patch may go unnoticed.
- **Latency**: a Live report is 0.7-1.8 s after the check, which is up to 2 s after the frame; a stop can
  therefore be 2-4 s behind the world. The phone's detector is the only sub-second path and it needs 2
  consecutive frames (~250 ms) and an approaching or large box in the centre 40 % of the frame.
- **Arrival is never confirmed**: "appears to be just ahead... reach forward carefully".
- **The guidance stops being guidance** when the view is unusable, when observations stop for 10 s, when
  the walk times out (5 min), when the phone disconnects or Live is lost: every one of these says so (or, on a
  disconnect, tells Live on resume) and the right response is to stand still.
- Reports come from a model: a report can be wrong in both directions (a missed obstacle, a phantom one).
  The 2-of-3 confirmation limits phantom stops for small objects; nothing limits a missed one.

## 6. What is NOT verified on a device, and the open main.py hooks

Not verified on a real phone in this pass (all logic is covered by fakes in `backend/tests/tools/test_walk.py`,
`backend/tests/core/test_nav_session.py`, `frontend/app/test/walk.*.test.ts`, `wire.test.ts`):

- iOS Safari: module Web Worker + `OffscreenCanvas` for the detector (the main-thread fallback exists),
  `createImageBitmap` with `resizeWidth` (Safari may ignore the resize options: the detector would then run
  on the full frame, slower), WebGL delegate -> CPU fallback timing, the ~4.5 MB model download from
  `storage.googleapis.com` on first run (no service-worker caching of it; offline first run = no guard, the
  camera and guidance keep working), `speechSynthesis` cutting in over Live audio on iOS, `navigator.vibrate`
  (absent on iPhone: the warn earcon is the instant cue).
- The real end-to-end timing of the nav session reopen (`OPEN_TIMEOUT_S` = 8 s against the 10 s gap) and of
  the watchdog line while Live is mid-answer (the URGENT note cuts the answer: measured once for notices, not
  for this line).
- The phone fast stop thresholds (`FAST_CLOSE_BOTTOM`, `FAST_CLOSE_H`, corridor) and the floor edge check
  were tuned on recorded frames, not on the shop floor.
- Walking with a real 60 s network hole: the session resume window and the Live resumption handle are
  exercised in `test_live_bridge.py` / `test_sessions_auth_main.py`, not together with a walk on a device.

Hooks the walk module provides but `main.py` does not call yet (owned by another engineer; the exact lines):

1. `main.py` hello branch, right after `bridge = await _start_live(session, handle)` when `resumed` is true:
   `await _lane_hook("aira_live.tools.walk", "on_session_resumed", session, bridge)` - tells Live a walk ended
   with the disconnect (see §3).
2. `main.live_frame`: the walk hook runs only `if sent and session.walking`. While the conversation bridge is
   reconnecting, `offer` returns False and the navigation session gets no frames although it is alive.
   Preferred: `if session.walking: await _lane_hook("aira_live.tools.walk", "on_live_frame", session, changed, frame.jpeg_b64, sent)`
   with `tools.walk.on_live_frame(session, changed=False, jpeg_b64=None, sent=True)` feeding the navigation
   session regardless of `sent` and stamping `frame_at` only when `sent` or the navigation session is alive.
   The walk module accepts the extra argument already.
