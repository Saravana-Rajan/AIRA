export type Route = "try" | "bench" | "app";

/**
 * Which top-level screen to render.
 * - `/try`, and `/?demo=1` (overrides §6): the judge demo page
 * - `/bench`: the benchmark page
 * - everything else: the shopkeeper app
 */
export function routeFor(pathname: string, search = ""): Route {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (p === "/try") return "try";
  if (p === "/bench") return "bench";
  if (p === "/" && new URLSearchParams(search).get("demo") === "1") return "try";
  return "app";
}
