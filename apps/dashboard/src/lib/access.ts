// Pure access resolution for the Shop Dashboard.
// Build overrides: no Firebase Auth. One owner runs one shop, so every visitor is signed out
// and reads murugan_dairy, read-only, as its owner. The family branch stays for a later
// signed-in mode and is never reached in this build.

export const DEMO_SHOP_ID = "murugan_dairy";

export type AccessRole = "owner" | "family";

export type Access =
  | { mode: "demo"; shopId: typeof DEMO_SHOP_ID; role: "owner" }
  | { mode: "family"; shopId: string; uid: string; role: AccessRole }
  | { mode: "denied"; reason: string };

const NO_SHOP = "No shop is linked to this account.";

function demo(): Access {
  return { mode: "demo", shopId: DEMO_SHOP_ID, role: "owner" };
}

export function resolveAccess(input: {
  uid: string | null;
  userDoc: { shopId?: string; role?: string } | null;
  wantDemo: boolean;
}): Access {
  // Signed-out visitors (and ?demo=1) only ever get the demo shop, whatever else is asked for.
  if (input.wantDemo || !input.uid) return demo();
  const doc = input.userDoc;
  if (doc?.role === "demo") return demo();
  if (doc && (doc.role === "owner" || doc.role === "family") && doc.shopId) {
    return { mode: "family", shopId: doc.shopId, uid: input.uid, role: doc.role };
  }
  return { mode: "denied", reason: NO_SHOP };
}
