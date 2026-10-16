# Low light: flashlight, low-light boost, "where is the light switch"

What AIRA does when the shop is dark, which flags switch it on, and what is and is not verified on a phone.
Flags: `AIRA_TORCH=1` (backend) with `VITE_AIRA_TORCH=1` (app build) for the flashlight, `AIRA_LOWLIGHT=1`
(backend) with `VITE_AIRA_LOWLIGHT=1` (app build) for the boost and the light-switch tool. All four are off by
default; off means no `light` message is sent, no `torch` command is sent or obeyed, no frame is boosted and
the tool is not offered to Gemini Live. Code: `frontend/app/src/camera/{lowlight,lightWatch,torch}.ts`,
`backend/aira_live/tools/light.py`, `backend/aira_live/vision/switches.py`.

## 1. Behaviour

```
phone                                                  Cloud Run (aira_live)                 Gemini Live
-----                                                  ---------------------                 -----------
probeGrid (32x24 gray, every 500 ms)
  '- lightWatch: raw mean luma -> createDarkDetector
       (a level changes only after 4 samples over >= 3 s;
        hysteresis 12 around DARK 40 / DIM 90)
  '- light {level, luma, torch}  ------------------>  tools.light.on_light
       on a level change, a torch change,                '- dark + torch off (AIRA_TORCH):
       a luma jump >= 20 (<= 1/s), or every 10 s             confirm.propose("generic",
       while the torch is on                                 "It's too dark. Shall I turn on
                                                             the flashlight?")  --[SYSTEM NOTE]-->  asks the owner
                                                         '- owner says "yes" -> confirm_action <--  tool call
                                                         '- committer light.torch_on:
  <-------------------------------------  torch {on: true}
torch.ts: track.getCapabilities().torch === true ?
  applyConstraints({advanced:[{torch:true}]})
  : "unsupported"
  '- light {.., torch: on|unsupported} ------------>  on_light: unsupported -> says once
                                                       "I can't turn on the flashlight on this phone"
```

- **Sustained, never one frame.** The level is decided on the phone (`lowlight.ts`): `dark` below luma 40,
  `dim` up to 90, `bright` at or above, each change needing 4 consecutive samples over at least 3 s, with 12
  units of hysteresis so a view hovering at a threshold does not flicker. The server trusts only this level.
- **Confirm before the torch.** A dark report makes the backend *propose* the flashlight once per dark episode
  (`confirm.propose`, kind `generic`). Only a spoken "yes" (`confirm_action`) or the phone's tapped `confirm`
  runs the committer, which sends the `torch` command. "No", silence (the proposal expires after 120 s) or a
  Stop never send it; the next dark episode (after a bright spell) may ask again.
- **Never pretend.** The phone turns the torch on only when the rear-camera track reports the capability and
  `applyConstraints` succeeds; otherwise it reports `unsupported`. The backend then says once "I can't turn on
  the flashlight on this phone" and never offers it again in that session. The chip shows "Flashlight on" only
  from the phone's own report (`screenState.torch`).
- **Off again.** Bright again with the torch on, sustained for `TORCH_OFF_BRIGHT_S = 20` s at a raw luma of at
  least `TORCH_OFF_LUMA = 120`, makes AIRA ask once before turning it off. The phone also switches it off by
  itself after `TORCH_AUTO_OFF_MS = 5 min` (`torch.ts`, phone-local so it never stays on because the server went
  away) and when the camera wiring is disposed.
- **Low-light boost (AIRA_LOWLIGHT).** In `dim` or `dark` light the phone brightens the pixels of UPLOADED frames
  (tool, live and describe frames; never the monitor preview) right before encoding: gain up to 2.5 toward a
  mean luma of 110 plus a small contrast stretch, on the canvas path (`capture.ts`). The quality check and the
  reported luma read the raw pixels first, so a boosted frame still earns "too dark" where it is. `exposureMode` /
  `exposureCompensation` constraints are not applied: they are not exposed by every browser and were not
  verified on the team's phones. The bump guard grabs its own frame straight from the `<video>` (`walk/start.ts`)
  and never sees the boost (`test/camera.boost.test.ts`, `test/walk.rawframe.test.ts`).
- **Honesty when it is still too dark.** The vision prompts already tell the model to set confidence below 0.5
  when the image is dark; the switch-board decision treats a detection under 0.5 as "unsure" and stops.

### "Where is the light switch?" (`light_switch_find`)

1. **With a shop map (Part A, branch `feat/routes`).** If `aira_live.tools.routes` is importable and its
   `async route_to(session, "switch_board")` returns a dict, the tool returns that route (`routed: true`) and the
   walk goes there first. The module is imported only if present: this branch works without it.
2. **In the dark.** With `AIRA_TORCH` on and the torch off, the tool proposes the flashlight first; after the
   phone's torch report (on or unsupported) the switch loop starts.
3. **Near the board.** The phone streams `guide` frames (about 1 a second). Each frame goes to the `point` model
   (`vision/switches.py`): a board box and a list of items, each `switch`, `socket` or `unknown` with a box.
   **Code decides** (`choose_switch`): a switch, by map label when one is given else by position from the left;
   never a socket; never a switch whose box overlaps a socket or whose centre is within 40 units of one; any
   `unknown` item or confidence under 0.5 → "Not sure which is a switch and which is a socket. I've stopped
   guiding." and the loop stops (the phone's tones and tracking stop with the `guide` stop). A board out of view
   says "I can't see the switch board. Turn slowly." once per loss and keeps looking.
4. **The press.** The existing hand guidance (`target` message → fingertip tracking → tones → `fingertip_in_target`)
   moves his finger; AIRA says "Your finger is on the second switch from the left. Press it." and remembers the
   raw luma. A rise of `BRIGHT_RISE_LUMA = 30` within `PRESS_WINDOW_S = 8` s → "The light is on now." and, with the
   torch on, the offer to turn it off. No rise → "Still dark. Try the next switch." and the next switch from
   the left; after the last one, "No more switches here." The loop stops by itself after 120 s.

Every spoken line is English facts to Gemini Live (said in whatever language the owner speaks); without a Live
session the phone captions the fixed Tamil / Hindi / English line (`system_lines.py` `light.*`, mirrored in
`frontend/app/src/i18n/strings.ts` `LIGHT`).

## 2. Contract

| Message | Direction | Fields | When |
|---|---|---|---|
| `light` | phone → server | `level: dark\|dim\|bright`, `luma: 0-255` (raw), `torch: off\|on\|unsupported` | level change, torch change, luma jump ≥ 20 (≤ 1/s), heartbeat every 10 s while the torch is on |
| `torch` | server → phone | `on: boolean` | only from the torch committers, after the owner's yes (on) or to turn off |

## 3. Platform support

| Phone / browser | Torch | Boost | Level report | Note |
|---|---|---|---|---|
| Android Chrome (rear camera) | expected: `getCapabilities().torch` is exposed by Chrome on devices with a flash | yes | yes | **not verified on device** |
| Android Firefox / Samsung Internet | unknown | yes | yes | **not verified on device** |
| iPhone Safari (WebKit) | **unsupported**: WebKit does not expose the torch capability or constraint; the phone reports `unsupported` and AIRA says so once | yes | yes | behaviour of the unsupported path is tested; the capability claim itself is **not verified on device** |
| Laptop webcam (judge demo) | unsupported (no flash) | yes | yes | tested in the app's test suite with a fake track |

Numbers in this document are the constants in the code, not measurements. No luma threshold here has been
measured against a real shop at night; tune `DARK_LUMA`, `DIM_LUMA`, `TORCH_OFF_LUMA` and `BRIGHT_RISE_LUMA`
with clips in `shared/bench/` on the team's test phone before relying on them.

## 4. Flags

| Flag | Where | Default | Effect |
|---|---|---|---|
| `AIRA_TORCH` | backend env | off | a dark report may *propose* the flashlight; the `torch` command exists only with it |
| `VITE_AIRA_TORCH` | app build | off | the phone reports `light` and obeys a `torch` command |
| `AIRA_LOWLIGHT` | backend env | off | `light_switch_find` is in the Live tool list |
| `VITE_AIRA_LOWLIGHT` | app build | off | the upload boost in dim light (no server needed) |

## 5. Tests

- Backend: `tests/tools/test_light.py` (proposal once per episode, no command without a yes, unsupported said
  once, bright-again offer, route delegation, switch loop safety paths, brightness before/after, flags off),
  `tests/vision/test_switches.py` (parsing, every `choose_switch` rule, the model call with a fake client),
  `tests/core/test_ws_mock.py` (the `light` message end to end on `/ws`).
- App: `test/camera.lowlight.test.ts` (sustained detector, hysteresis, no flicker, boost, reporter),
  `test/camera.torch.test.ts` (command only, capability-gated, unsupported, auto-off),
  `test/camera.boost.test.ts` and `test/walk.rawframe.test.ts` (raw quality check, preview and bump guard never
  boosted), `test/camera.wire.lowlight.test.ts` (flags off ⇒ no behaviour change; flags on end to end),
  `test/screenState.torch.test.ts` (the chip).

## 6. Not done yet

- Route-to-switch wiring once Part A merges: implement `aira_live.tools.routes.route_to(session, place)` or
  point `tools.light.ROUTE_MODULE` at the real module; the tool already delegates when it is importable.
- On-device verification of the torch on Android Chrome and of the `unsupported` path on iPhone Safari.
- Measuring the luma thresholds and the boost gain on the test phone in a real dark shop.
