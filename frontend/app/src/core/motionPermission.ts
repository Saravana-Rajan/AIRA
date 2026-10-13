// Motion and orientation sensor access. iOS Safari (13+) hides `deviceorientation` and `devicemotion` until the
// page calls DeviceOrientationEvent.requestPermission() / DeviceMotionEvent.requestPermission(), and that call only
// shows its prompt inside a user gesture. requestMotionAccess() runs both requests synchronously inside the Start
// tap; the tilt hints (camera/tilt.ts) and the walking detector (walk/motion.ts) later await the same answer through
// motionAccess() and listen only after "granted". Browsers without requestPermission (Android Chrome) need no
// prompt. A refusal or a failed prompt just means `false`: the sensor features stay off, nothing throws.

export type MotionKind = "orientation" | "motion";

const GLOBAL_NAME: Record<MotionKind, string> = { orientation: "DeviceOrientationEvent", motion: "DeviceMotionEvent" };

type Requester = () => Promise<string> | string;
type Entry = { fn: Requester; p: Promise<boolean> };

// One request per sensor API. Keyed by the requestPermission function itself, so a new browser object (or a test
// stub) asks again. A request that threw (not inside a gesture) is forgotten, so the next tap can ask again.
const asked = new Map<MotionKind, Entry>();

/**
 * Resolves true when this sensor can be listened to: granted on iOS, or no permission needed. False when the API is
 * missing, the owner refused, or the prompt failed. The first call inside a user gesture shows the iOS prompt; later
 * calls share its answer. Never throws or rejects.
 */
export function motionAccess(kind: MotionKind, g: typeof globalThis = globalThis): Promise<boolean> {
  let api: { requestPermission?: Requester } | undefined;
  try {
    api = (g as unknown as Record<string, { requestPermission?: Requester } | undefined>)[GLOBAL_NAME[kind]];
  } catch {
    return Promise.resolve(false);
  }
  if (!api) return Promise.resolve(false);
  const fn = api.requestPermission;
  if (typeof fn !== "function") return Promise.resolve(true); // no prompt on this browser
  const hit = asked.get(kind);
  if (hit && hit.fn === fn) return hit.p;
  let raw: Promise<string>;
  try {
    raw = Promise.resolve(fn.call(api));
  } catch (e) {
    raw = Promise.reject(e);
  }
  const entry: Entry = {
    fn,
    p: raw.then(
      (r) => r === "granted",
      () => {
        if (asked.get(kind) === entry) asked.delete(kind);
        return false;
      },
    ),
  };
  asked.set(kind, entry);
  return entry.p;
}

/** True when this browser gates the sensors behind a permission prompt (iOS Safari). */
export function motionNeedsPermission(g: typeof globalThis = globalThis): boolean {
  try {
    return (["orientation", "motion"] as MotionKind[]).some((k) => {
      const api = (g as unknown as Record<string, { requestPermission?: unknown } | undefined>)[GLOBAL_NAME[k]];
      return typeof api?.requestPermission === "function";
    });
  } catch {
    return false;
  }
}

/**
 * Call synchronously inside a user gesture (the Start tap): asks for orientation and motion access at once, so iOS
 * shows its single "Motion & Orientation" prompt. Does nothing where no prompt exists. Never throws.
 */
export function requestMotionAccess(g: typeof globalThis = globalThis): void {
  void motionAccess("orientation", g);
  void motionAccess("motion", g);
}

/** Tests only: forget every earlier answer. */
export function __resetMotionAccess(): void {
  asked.clear();
}
