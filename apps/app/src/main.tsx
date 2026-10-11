// Entry point. Three routes (demo/router.ts):
//   /try (or /?demo=1)  the judge demo page; it wires and connects the app itself on the first tap
//   /bench              the benchmark export page; no owner socket is opened
//   everything else     the shopkeeper app: onboarding, then the single gesture surface
// AppShell / TryAira run `onWired` inside the Start tap, which is where the camera, hand guidance, the bump
// guard (owner app only) and the in-app benchmark collector join the wired voice app.
import { render } from "preact";
import { AppShell } from "./ui/AppShell";
import { TryAira } from "./demo/TryAira";
import { BenchPage } from "./bench/BenchPage";
import { CameraPage } from "./camera-mode/CameraPage";
import { routeFor } from "./demo/router";
import { wireCameraIntoApp } from "./camera/wire";
import { installBenchIfEnabled } from "./bench/install";
import { registerServiceWorker } from "./pwa/registerSW";

void registerServiceWorker(); // production only; never throws

const route = routeFor(location.pathname, location.search);
const root = document.getElementById("root")!;

if (route === "bench") {
  render(<BenchPage />, root);
} else if (route === "camera") {
  // Shop-camera mode: a second phone on a stand streams frames at a low rate; no mic, no Live session.
  render(<CameraPage />, root);
} else if (route === "try") {
  // No bump guard on the demo page: no object-detector download, nobody is walking.
  render(<TryAira onWired={(w) => { wireCameraIntoApp(w, { bumpGuard: false }); }} />, root);
} else {
  render(
    <AppShell
      onWired={(w) => {
        wireCameraIntoApp(w);
        installBenchIfEnabled(); // records only when /bench switched recording on
      }}
    />,
    root,
  );
}
