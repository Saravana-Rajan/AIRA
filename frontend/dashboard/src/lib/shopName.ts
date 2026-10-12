/** The display name of a shop id, for the header and the Live page; the id itself when no name is known. */
export const SHOP_NAMES: Record<string, string> = { sunrise_dairy: "Sunrise Dairy" };

export function shopName(shopId: string | null | undefined): string {
  if (!shopId) return "AIRA";
  return SHOP_NAMES[shopId] ?? shopId;
}
