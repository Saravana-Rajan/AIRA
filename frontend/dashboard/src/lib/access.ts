// Access for the Shop Dashboard. Build overrides: no Firebase Auth. There is one shop, so every visitor
// reads it, read-only, as its owner; nothing in the URL or a user record can point the page elsewhere.

/** The one shop's Firestore id (shops/{id}/...), the same value aira-live serves as its demo shop. */
export const DEMO_SHOP_ID = "sunrise_dairy";
/** The one shop's display name (header, menu, Live page, phone app). */
export const SHOP_NAME = "AIRA";

export type AccessRole = "owner";

export type Access = { mode: "demo"; shopId: typeof DEMO_SHOP_ID; role: AccessRole };

/** Always the one shop as its owner. The argument (an older signature's sign-in state) is ignored. */
export function resolveAccess(_input?: unknown): Access {
  return { mode: "demo", shopId: DEMO_SHOP_ID, role: "owner" };
}
