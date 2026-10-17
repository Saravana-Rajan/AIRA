// Hands-free start: earphone button (web), volume chord and wake word (pure logic for a native shell).
// Flags: handsfree/flags.ts. Docs: shared/docs/ops/handsfree.md, shared/docs/proposals/native-android-shell.md.
export { handsfreeFlags, flagOn, FLAG_ENV, FLAGS_OFF, type HandsfreeFlags } from "./flags";
export { attachMediaButton, audioKeepAlive, keepAliveWav, MEDIA_ACTIONS, type MediaButton, type MediaButtonOptions, type MediaSessionLike, type KeepAlive } from "./mediaButton";
export { VolumeChordDetector, DEFAULT_VOLUME_CHORD, type VolumeChordConfig, type VolumeChordResult, type VolumeKeyEvent } from "./volumeChord";
export { WakeWordController, DEFAULT_WAKE_WORD, type WakeEngine, type WakeWordCallbacks, type WakeWordOptions, type WakeWordState } from "./wakeWord";
export { HANDSFREE_STRINGS } from "./strings";
