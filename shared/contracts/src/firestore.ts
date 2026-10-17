// Firestore document types. Field names mirror spec §5 exactly.
// Database id: "aira". Paths are relative to shops/{shopId}/ unless noted.
import type {
  Actor, EventTopic, LedgerReason, OrderState, PaymentState, ProductUnit, SaleState, Verbosity,
} from "./enums";

/** A Firestore Timestamp as read by the JS SDK (or serialised by the server). */
export interface FsTimestampLike { seconds: number; nanoseconds: number; toDate?: () => Date }
/** Time fields: ISO string (fixtures, seeds) or a Firestore Timestamp (server timestamps). */
export type FsTime = string | FsTimestampLike;

export function fsTimeToDate(t: FsTime): Date {
  if (typeof t === "string") return new Date(t);
  if (typeof t.toDate === "function") return t.toDate();
  return new Date(t.seconds * 1000 + Math.floor(t.nanoseconds / 1_000_000));
}

export type I18nAliases = { ta: string[]; hi: string[]; en: string[] };

/** shops/{shopId} */
export interface Shop {
  name: string; ownerUid: string; city: string; currency: "INR"; timezone: "Asia/Kolkata";
  /** Languages this shop usually speaks (BCP-47 codes, e.g. ["ta", "en"]). Guides, never limits, detection. */
  languages?: string[];
}

/** shops/{shopId}/products/{sku} */
export interface Product {
  sku: string; name: string; brand: string; unit: ProductUnit;
  packSizeL?: number; packSizeG?: number;
  price: number; costPrice: number; reorderThreshold: number; shelfLifeDays: number;
  aliases: I18nAliases;
  /** Optional litres/day used by the 30-day demo sales backfill. */
  demoDailyBase?: number;
}

/** shops/{shopId}/suppliers/{id} */
export interface Supplier { id: string; name: string; phone: string; channel: "whatsapp" | "telegram" | "call"; products: string[] }

/** shops/{shopId}/orders/{id} */
export interface OrderItem { sku: string; qty: number; unit?: string; name?: string; agreedPrice?: number }
/** The vendor's last reply, as the webhooks store it (messaging/webhooks.py). */
export interface VendorReply { text: string; at: FsTime; channel: "whatsapp" | "telegram" }
/** The delivery time the owner asked for: his words, plus an ISO IST datetime (or bare date) when they parsed. */
export interface DeliveryBy { text: string; at: string | null }
export interface Order {
  id: string; supplierId: string; supplierName?: string; items: OrderItem[]; state: OrderState;
  channelMsgId?: string; vendorReply?: VendorReply; idemKey: string; createdAt: FsTime; updatedAt: FsTime;
  deliveryBy?: DeliveryBy | null; note?: string | null;
}

/** shops/{shopId}/deliveries/{id}: method and confidence are null until the item has been counted. */
export interface DeliveryItem {
  sku: string; name?: string; unit?: string; expectedQty: number; countedQty: number | null;
  method: "one_by_one" | "crate" | "owner_confirmed" | null; confidence: number | null; countIdem?: string | null;
}
export interface InvoiceLineDoc { name: string; qty: number; unit: string; unit_price: number; line_total: number }
/** What invoice_check stores: the agreed reading of the bill (two reads must agree), never the images. */
export interface InvoiceSnapshot {
  supplier: string | null; date: string | null; total: number | null; agreed: boolean; lines: InvoiceLineDoc[];
}
export interface Discrepancy { sku?: string; field?: string; expected: unknown; actual: unknown }
export interface Delivery {
  id: string; orderId: string; items: DeliveryItem[]; invoice: InvoiceSnapshot | null;
  discrepancies: Discrepancy[]; state: OrderState;
}

/** shops/{shopId}/inventory/{sku} */
export interface InventoryBatch { batchId: string; qty: number; expiryDate: string }
export interface InventoryDoc { sku: string; onHand: number; batches: InventoryBatch[]; updatedAt: FsTime }

/** shops/{shopId}/ledger/{txnId} */
export interface LedgerTxn {
  id: string; sku: string; delta: number; reason: LedgerReason; refType: string; refId: string;
  idemKey: string; at: FsTime; actor: Actor;
}

/** shops/{shopId}/locations/{sku} */
export interface Location { sku: string; zone: string; confirmedBy: "user" | "model"; confirmedAt: FsTime; confidence: number }

/** shops/{shopId}/sales/{id} */
export interface SaleItem { sku: string; qty: number; unitPrice: number; lineTotal: number }
export interface Sale {
  id: string; items: SaleItem[]; total: number; state: SaleState; paymentId?: string; idemKey: string;
  createdAt: FsTime; completedAt?: FsTime;
}

/** shops/{shopId}/payments/{id} */
export interface PaymentEvidence { type: "cash_count" | "soundbox" | "user_confirm"; detail: string }
export interface Payment {
  id: string; direction: "in" | "out"; method: "cash" | "upi"; amount: number; state: PaymentState;
  evidence: PaymentEvidence | null; refType: string; refId: string;
}

/** shops/{shopId}/memoryEvents/{id} */
export interface MemoryEvent { id?: string; type: string; text: string; refType?: string; refId?: string; at: FsTime }

/** shops/{shopId}/auditLogs/{id} */
export interface AuditLog {
  id: string; actor: Actor; action: string; refType: string; refId: string; outcome: string;
  evidenceUri?: string; at: FsTime;
}

/** shops/{shopId}/prefs/main */
export interface ShopPrefs {
  speechRate: number; verbosity: Verbosity;
  productAliases: Record<string, string[]>; supplierAliases: Record<string, string[]>;
}

/** shops/{shopId}/events/{autoId}: analytics events (replaces Pub/Sub + BigQuery). */
export interface ShopEvent { topic: EventTopic; payload: Record<string, unknown>; ts: FsTime }

/** config/runtime: runtime flags (replaces Remote Config). Every field is optional. */
export interface RuntimeConfig {
  pointing_model?: string; count_model?: string; live_model?: string; whatsapp_enabled?: boolean;
  /** Overrides every shop's expected languages (shops/{shopId}.languages). */
  languages?: string[];
}
