export type ScenarioMediaKind = "video" | "image" | "audio";

export interface Scenario {
  id: string;
  title: string;
  blurb: string;
  /** Sent as a `text` message, exactly as Sam would say it. */
  prompt: string;
  media?: { kind: ScenarioMediaKind; src: string };
}

export const SCENARIOS: Scenario[] = [
  { id: "order", title: "1 · Order stock by voice", blurb: "AIRA reads the order back, then sends it to the vendor on WhatsApp after you confirm.",
    prompt: "Order 10 litres of Sakthi curd from the Sakthi vendor." },
  { id: "delivery-one", title: "2 · Count the delivery, one by one", blurb: "Each pack is counted live; only verified stock is recorded.",
    prompt: "The Sakthi curd delivery has arrived. Count it one by one.", media: { kind: "video", src: "/demo/delivery_one_by_one.mp4" } },
  { id: "delivery-crate", title: "3 · Count a crate", blurb: "One photo of a crate; AIRA asks for another view if unsure.",
    prompt: "Count the Sakthi curd crate.", media: { kind: "image", src: "/demo/crate_sakthi_curd.jpg" } },
  { id: "invoice-ok", title: "4 · Check the invoice (correct)", blurb: "Two independent reads, then code compares them with the order and the count.",
    prompt: "Check the Sakthi invoice.", media: { kind: "image", src: "/demo/invoice_ok.jpg" } },
  { id: "invoice-wrong", title: "5 · Check the invoice (wrong)", blurb: "The bill says 12 litres but 10 arrived. AIRA catches it.",
    prompt: "Check this Sakthi invoice.", media: { kind: "image", src: "/demo/invoice_wrong_qty.jpg" } },
  { id: "pay-vendor", title: "6 · Pay the vendor in cash", blurb: "Notes in hand are read twice and totalled in code.",
    prompt: "Pay the Sakthi vendor in cash.", media: { kind: "image", src: "/demo/cash_vendor_500x2.jpg" } },
  { id: "sale-guide", title: "7 · Guide the hand to the product", blurb: "Location memory plus live pointing guide the hand to the curd.",
    prompt: "A customer wants 2 litres of Sakthi curd. Guide me.", media: { kind: "video", src: "/demo/sale_guidance.mp4" } },
  { id: "sale-cash", title: "8 · Customer pays cash", blurb: "AIRA reads the note and gives the exact change.",
    prompt: "The customer is paying cash for the curd.", media: { kind: "image", src: "/demo/cash_customer_100.jpg" } },
  { id: "sale-upi", title: "9 · Customer pays by UPI", blurb: "AIRA listens to the shop's payment soundbox; a screenshot is never trusted.",
    prompt: "The customer is paying by UPI.", media: { kind: "audio", src: "/demo/soundbox_upi_60.wav" } },
  { id: "summary", title: "10 · End of day", blurb: "Sales, top seller, low stock and tomorrow's suggested order.",
    prompt: "How was today?" },
];

export const DEMO_MEDIA_FILES: string[] = SCENARIOS.flatMap((s) => (s.media ? [s.media.src] : []));

export interface DayPart {
  id: "stock" | "counter" | "close";
  title: string;
  /** In the order they happen; together the parts cover SCENARIOS exactly once. */
  scenarioIds: string[];
}

/** Sam's day in three parts, for grouping the scene cards on the landing page. */
export const DAY_PARTS: DayPart[] = [
  { id: "stock", title: "Morning: stock comes in",
    scenarioIds: ["order", "delivery-one", "delivery-crate", "invoice-ok", "invoice-wrong", "pay-vendor"] },
  { id: "counter", title: "At the counter", scenarioIds: ["sale-guide", "sale-cash", "sale-upi"] },
  { id: "close", title: "Closing time", scenarioIds: ["summary"] },
];
