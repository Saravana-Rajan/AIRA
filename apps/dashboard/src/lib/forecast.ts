// Reorder suggestions from aira-live: GET {base}/api/forecast?shop={shopId}
// -> { items: ForecastItem[], demoHistory: boolean }. Any failure yields null so the page can say
// "Forecast unavailable". demoHistory is true when backfilled demo sales (scripts/backfill_demo_sales.py,
// ledger rows labelled demo:true) feed the numbers: the card then says "demo history", never real sales.

export interface ForecastItem { sku: string; name: string; dailyDemand: number; onHand: number; suggestedOrder: number }
export interface Forecast { items: ForecastItem[]; demoHistory: boolean }

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function parseForecast(json: unknown): Forecast | null {
  if (typeof json !== "object" || json === null) return null;
  const { items, demoHistory } = json as { items?: unknown; demoHistory?: unknown };
  if (!Array.isArray(items)) return null;
  const out: ForecastItem[] = [];
  for (const it of items) {
    if (typeof it !== "object" || it === null) return null;
    const x = it as Record<string, unknown>;
    if (typeof x.sku !== "string" || typeof x.name !== "string") return null;
    if (!isNum(x.dailyDemand) || !isNum(x.onHand) || !isNum(x.suggestedOrder)) return null;
    out.push({ sku: x.sku, name: x.name, dailyDemand: x.dailyDemand, onHand: x.onHand, suggestedOrder: x.suggestedOrder });
  }
  return { items: out, demoHistory: demoHistory === true };
}

export async function fetchForecast(
  base: string, shopId: string, fetchImpl: typeof fetch = fetch, timeoutMs = 5000,
): Promise<Forecast | null> {
  const url = `${base.replace(/\/+$/, "")}/api/forecast?shop=${encodeURIComponent(shopId)}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetchImpl(url, { signal: ctrl.signal });
    if (!res.ok) return null;
    return parseForecast(await res.json());
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
