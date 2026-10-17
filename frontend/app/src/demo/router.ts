export type Route = "try" | "bench" | "camera" | "app";

/**
 * Which top-level screen to render.
 * - `/try`, and `/?demo=1` (overrides §6): the judge demo page
 * - `/bench`: the benchmark page
 * - `/camera`: shop-camera mode (a second phone on a stand; shared/docs/ops/cctv.md)
 * - everything else: the shopkeeper app
 */
export function routeFor(pathname: string, search = ""): Route {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (p === "/try") return "try";
  if (p === "/bench") return "bench";
  if (p === "/camera") return "camera";
  if (p === "/" && new URLSearchParams(search).get("demo") === "1") return "try";
  return "app";
}
