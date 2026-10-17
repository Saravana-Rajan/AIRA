/** Four-point spark from the AIRA logo, centred in a 100 x 100 box. */
const SPARK = "M50 4 C54 34 66 46 96 50 C66 54 54 66 50 96 C46 66 34 54 4 50 C34 46 46 34 50 4 Z";

/** Three sound-wave rings from the logo, drawn as arcs on both sides of the orb. */
const WAVES = [
  { r: 47, cls: "orb__wave orb__wave--3" },
  { r: 41, cls: "orb__wave orb__wave--2" },
  { r: 35, cls: "orb__wave orb__wave--1" },
];

/**
 * The centre voice orb. Purely visual (aria-hidden): its colour follows the status set on the
 * parent `.screen[data-status]`, and `level` (0..1, mic or speaker loudness) swells it.
 */
export function VoiceOrb({ level = 0 }: { level?: number }) {
  const lv = Math.min(1, Math.max(0, Number.isFinite(level) ? level : 0));
  return (
    <span class="orb" aria-hidden="true" style={{ "--level": String(lv) }}>
      <svg class="orb__waves" viewBox="0 0 100 100" focusable="false">
        {WAVES.map((w) => (
          <circle class={w.cls} cx="50" cy="50" r={w.r} pathLength={100} />
        ))}
      </svg>
      <span class="orb__core">
        <span class="orb__swirl" />
        <svg class="orb__spark" viewBox="0 0 100 100" focusable="false">
          <path d={SPARK} />
        </svg>
      </span>
    </span>
  );
}
