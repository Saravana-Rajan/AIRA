// apps/app/src/guidance/mediapipe.ts
// The single main-thread entry to @mediapipe/tasks-vision (hand tracking and the bump-guard fallback).
// Load it ONLY with a dynamic import(). That keeps MediaPipe in a lazy chunk, out of the start-up bundle,
// and lets every module that uses it be imported (and unit-tested) without loading MediaPipe.
export { FilesetResolver, HandLandmarker, ObjectDetector } from "@mediapipe/tasks-vision";
