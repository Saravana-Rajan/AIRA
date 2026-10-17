// Where AIRA's voice comes out is the OS's choice (no setSinkId on Chrome Android or iOS Safari): earphones
// when a headset is paired, the loudspeaker otherwise. This module only notices the headset coming and
// going, from the device list's labels, so core/wire can reopen the mic (the browser re-routes on open) and
// warn the owner once when the earphones drop: a blind walker must never silently lose a "Stop".

/** A label that belongs to a headset of any kind (Bluetooth, wired, USB-C). */
const HEADSET = /bluetooth|airpods|buds|headset|headphone|earphone|hands-?free|wired/i;

/** Pure: true when any audio input or output in the list is a headset. Labels are empty before permission. */
export function hasHeadset(devices: MediaDeviceInfo[]): boolean {
  return devices.some((d) => (d.kind === "audioinput" || d.kind === "audiooutput") && HEADSET.test(d.label));
}

/** What the watcher needs from navigator.mediaDevices. The listener methods are optional: older WebViews lack them. */
export interface DeviceSource {
  enumerateDevices(): Promise<MediaDeviceInfo[]>;
  addEventListener?(type: "devicechange", fn: () => void): void;
  removeEventListener?(type: "devicechange", fn: () => void): void;
}

/**
 * Calls `onChange(present)` on every headset transition (false→true, true→false), never for a devicechange
 * that leaves the answer as it was. The first enumeration only seeds the state. Enumerations run one at a
 * time, in event order. Returns a dispose that removes the listener and drops any result still in flight.
 * No-op (returns a dispose that does nothing) where `md` is missing.
 */
export function watchHeadset(md: DeviceSource | null | undefined, onChange: (present: boolean) => void): () => void {
  if (!md || typeof md.enumerateDevices !== "function") return () => {};
  let known: boolean | null = null; // null until the seed enumeration lands
  let disposed = false;
  let chain: Promise<void> = Promise.resolve();
  const check = () => {
    chain = chain.then(async () => {
      if (disposed) return;
      let present: boolean;
      try {
        present = hasHeadset(await md.enumerateDevices());
      } catch {
        return; // enumeration blocked: keep the last answer
      }
      if (disposed) return;
      const was = known;
      known = present;
      if (was !== null && was !== present) onChange(present);
    });
  };
  check();
  const onDeviceChange = () => check();
  if (typeof md.addEventListener === "function") md.addEventListener("devicechange", onDeviceChange);
  return () => {
    if (disposed) return;
    disposed = true;
    if (typeof md.removeEventListener === "function") md.removeEventListener("devicechange", onDeviceChange);
  };
}
