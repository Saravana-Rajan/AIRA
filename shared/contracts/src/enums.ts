// Shared enums and fixed lists. Mirrored in backend/aira_live/contracts.py.
// Every list is frozen at runtime and typed as a readonly tuple.

export const LANGS = Object.freeze(["ta", "hi", "en"] as const);
export type Lang = (typeof LANGS)[number];

export const VERBOSITIES = Object.freeze(["short", "detail"] as const);
export type Verbosity = (typeof VERBOSITIES)[number];

export const FRAME_PURPOSES = Object.freeze(["ask", "count", "point", "identify", "invoice", "cash", "guide", "describe", "identify_held"] as const);
export type FramePurpose = (typeof FRAME_PURPOSES)[number];

export const CUE_NAMES = Object.freeze([
  "ack", "thinking", "done", "warn", "match", "mismatch", "unsure", "interrupted", "reconnect", "offline",
] as const);
export type CueName = (typeof CUE_NAMES)[number];

export const ENTITIES = Object.freeze(["order", "delivery", "sale", "payment", "inventory"] as const);
export type Entity = (typeof ENTITIES)[number];

export const CONFIRM_KINDS = Object.freeze(["order", "payment", "sale", "discrepancy", "generic"] as const);
export type ConfirmKind = (typeof CONFIRM_KINDS)[number];

export const CONFIRM_ANSWERS = Object.freeze(["yes", "no"] as const);
export type ConfirmAnswer = (typeof CONFIRM_ANSWERS)[number];

export const CLIENT_EVENT_NAMES = Object.freeze(["visibility", "network", "fingertip_in_target"] as const);
export type ClientEventName = (typeof CLIENT_EVENT_NAMES)[number];

export const TRANSCRIPT_ROLES = Object.freeze(["user", "aira"] as const);
export type TranscriptRole = (typeof TRANSCRIPT_ROLES)[number];

export const ERROR_CODES = Object.freeze([
  "network", "model_timeout", "camera", "permission", "unsupported", "internal",
] as const);
export type ErrorCode = (typeof ERROR_CODES)[number];

// Who drew a box: the phone's on-device MediaPipe detector, Gemini Robotics-ER 2, or the phone's bump guard.
export const BOX_SOURCES = Object.freeze(["mediapipe", "er2", "bump"] as const);
export type BoxSource = (typeof BOX_SOURCES)[number];
export const BOX_SEVERITIES = Object.freeze(["danger"] as const);
export type BoxSeverity = (typeof BOX_SEVERITIES)[number];

export const ORDER_STATES = Object.freeze([
  "DRAFT", "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "DISCREPANCY",
  "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED",
] as const);
export type OrderState = (typeof ORDER_STATES)[number];

export const SALE_STATES = Object.freeze(["SALE_PENDING", "SALE_COMPLETED", "SALE_VOID"] as const);
export type SaleState = (typeof SALE_STATES)[number];

export const PAYMENT_STATES = Object.freeze(["PENDING", "CONFIRMED", "FAILED"] as const);
export type PaymentState = (typeof PAYMENT_STATES)[number];

export const LEDGER_REASONS = Object.freeze(["DELIVERY", "SALE", "RETURN", "ADJUST", "EXPIRED"] as const);
export type LedgerReason = (typeof LEDGER_REASONS)[number];

export const PRODUCT_UNITS = Object.freeze(["L", "pack", "jar", "box"] as const);
export type ProductUnit = (typeof PRODUCT_UNITS)[number];

export const ACTORS = Object.freeze(["owner", "aira", "system"] as const);
export type Actor = (typeof ACTORS)[number];

export const EVENT_TOPICS = Object.freeze(["sales", "inventory-movements", "orders", "discrepancies", "alerts"] as const);
export type EventTopic = (typeof EVENT_TOPICS)[number];

// Fixed demo data (identical in the catalog seed, vision prompts and tests).
export const DEMO_SHOP_ID = "sunrise_dairy";
export const DEMO_SKUS = Object.freeze([
  "aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "aavin_buttermilk_200ml",
  "aavin_ghee_200ml", "aavin_ghee_500ml", "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box",
] as const);
export type DemoSku = (typeof DEMO_SKUS)[number];
export const SUPPLIER_IDS = Object.freeze(["aavin_vendor", "sakthi_vendor", "arun_distributor"] as const);
export type SupplierId = (typeof SUPPLIER_IDS)[number];

// Spatial coordinates: point [y, x], box [ymin, xmin, ymax, xmax], integers 0..NORM_MAX.
export const NORM_MAX = 1000;
// A `boxes` message carries at most this many objects (the detectors return 8; ER 2 at most a dozen).
export const MAX_BOXES = 32;
