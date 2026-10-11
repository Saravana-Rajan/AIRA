// Registers the AIRA service worker (public/sw.js). Production builds only, never throws:
// a page that cannot install offline support must still work as a normal web page.

type ServiceWorkerNavigator = Pick<Navigator, "serviceWorker">;

const viteEnvIsProd = (): boolean =>
  (import.meta as unknown as { env?: { PROD?: boolean } }).env?.PROD === true;

export async function registerServiceWorker(
  nav: ServiceWorkerNavigator | undefined = typeof navigator !== "undefined" ? navigator : undefined,
  isProd: boolean = viteEnvIsProd(),
): Promise<ServiceWorkerRegistration | null> {
  if (!isProd || !nav || !("serviceWorker" in nav) || !nav.serviceWorker) return null;
  try {
    // updateViaCache "none": the browser re-checks sw.js AND sw-strategy.js (importScripts)
    // against the network on every update check, so a new deploy is never masked by HTTP cache.
    return await nav.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" });
  } catch {
    return null;
  }
}
