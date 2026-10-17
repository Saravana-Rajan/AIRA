import { SHOP_NAME } from "./access";

/** The one shop's display name, for the header and the Live page. The id argument is legacy and ignored. */
export function shopName(_shopId?: string | null): string {
  return SHOP_NAME;
}
