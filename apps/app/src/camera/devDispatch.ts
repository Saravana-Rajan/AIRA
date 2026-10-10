// apps/app/src/camera/devDispatch.ts — dev/test builds only: drive the app from chrome://inspect on the phone,
// e.g. __aira.dispatch({t:"count",sku:"s",counted:1,expected:3,final:false,confidence:0.9})
import type { ServerMsg } from "@aira/contracts";

type DevGlobal = { __aira?: { dispatch: (m: ServerMsg) => void } };

/** Installs `window.__aira.dispatch`; returns an uninstall fn. */
export function installFake(dispatch: (m: ServerMsg) => void): () => void {
  const g = globalThis as unknown as DevGlobal;
  const hook = { dispatch };
  g.__aira = hook;
  return () => {
    if (g.__aira === hook) delete g.__aira;
  };
}
