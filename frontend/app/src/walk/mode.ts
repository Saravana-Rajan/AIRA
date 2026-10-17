// frontend/app/src/walk/mode.ts
// Walking mode: the persisted on/off switch for the bump guard. On by default. Toggled from the server
// (a `state` message whose summary is "WALKING_MODE on|off", like OPEN_URL), from a spoken command the
// client understands itself, or from settings. Kept outside core/settings so the guard owns it.

export const WALKING_MODE_KEY = "aira.walkingMode";
export const WALKING_MODE_PREFIX = "WALKING_MODE ";

const defaultStore = (): Storage | null => {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
};

/** true unless "off" was saved. Never throws. */
export function loadWalkingMode(store: Storage | null = defaultStore()): boolean {
  try {
    return store?.getItem(WALKING_MODE_KEY) !== "off";
  } catch {
    return true;
  }
}

/** Persists the switch; storage failures are ignored. */
export function saveWalkingMode(on: boolean, store: Storage | null = defaultStore()): void {
  try {
    store?.setItem(WALKING_MODE_KEY, on ? "on" : "off");
  } catch {
    /* private mode or quota: stays in memory */
  }
}

const ON = /\b(on|start|enable)\b/;
const OFF = /\b(off|stop|disable)\b/;

/**
 * "WALKING_MODE on" / "WALKING_MODE off" from the server, or a short spoken form such as
 * "walking mode off", "turn walking mode on", "stop walking mode". null when it is not this command.
 */
export function parseWalkingModeCommand(text: string): boolean | null {
  const s = text.trim();
  let rest: string;
  if (s.startsWith(WALKING_MODE_PREFIX)) rest = s.slice(WALKING_MODE_PREFIX.length).toLowerCase();
  else {
    const lower = s.toLowerCase();
    if (!/\bwalking[ -]?mode\b/.test(lower)) return null;
    rest = lower;
  }
  if (OFF.test(rest)) return false;
  if (ON.test(rest)) return true;
  return null;
}

type Listener = (on: boolean) => void;

function createStore() {
  let store: Storage | null = defaultStore();
  let value: boolean | null = null;
  const listeners = new Set<Listener>();
  const read = () => (value === null ? (value = loadWalkingMode(store)) : value);
  return {
    get(): boolean { return read(); },
    set(on: boolean): void {
      if (read() === on) return;
      value = on;
      saveWalkingMode(on, store);
      for (const fn of listeners) {
        try { fn(on); } catch (e) { console.error("[aira] walking mode listener failed", e); }
      }
    },
    subscribe(fn: Listener): () => void {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
    /** Tests: use another storage (or none) and forget the cached value and listeners. */
    reset(s: Storage | null = defaultStore()): void {
      store = s;
      value = null;
      listeners.clear();
    },
  };
}

/** The app-wide walking-mode switch. */
export const walkingMode = createStore();
