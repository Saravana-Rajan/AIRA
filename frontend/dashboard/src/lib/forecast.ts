// Reorder suggestions from aira-live: GET {base}/api/forecast?shop={shopId}
// -> { items: ForecastItem[], demoHistory: boolean }. Any failure (after the retries below) yields null so
// the page can say "Forecast unavailable". demoHistory is true when backfilled demo sales (shared/scripts/backfill_demo_sales.py,
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

/**
 * How hard to try. aira-live runs on Cloud Run with min-instances 0: the first request after a quiet spell
 * wakes the server, which takes more than 5 s; once awake it answers in about 0.2 s. So the first try gets
 * 15 s, and a timeout, network error or 5xx is retried after a short pause before giving up.
 */
export interface RetryPlan {
  /** Time limit for each try, in order; its length is the number of tries. */
  timeoutsMs: readonly number[];
  /** Pause before each retry: [before try 2, before try 3, …]. */
  backoffMs: readonly number[];
}
export const FORECAST_RETRY: RetryPlan = { timeoutsMs: [15_000, 10_000, 10_000], backoffMs: [1_000, 2_000] };

type Attempt = { forecast: Forecast | null; retry: boolean };

/** One request. retry is true only when another try could help: a timeout, a network error, 408/429/5xx. */
async function attempt(url: string, fetchImpl: typeof fetch, timeoutMs: number): Promise<Attempt> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetchImpl(url, { signal: ctrl.signal });
    } catch {
      return { forecast: null, retry: true };
    }
    if (!res.ok) return { forecast: null, retry: res.status >= 500 || res.status === 408 || res.status === 429 };
    try {
      return { forecast: parseForecast(await res.json()), retry: false };
    } catch {
      // Malformed JSON will not fix itself; a body cut off by our own timeout might.
      return { forecast: null, retry: ctrl.signal.aborted };
    }
  } finally {
    clearTimeout(timer);
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function fetchForecast(
  base: string, shopId: string, fetchImpl: typeof fetch = fetch, plan: RetryPlan = FORECAST_RETRY,
): Promise<Forecast | null> {
  const url = `${base.replace(/\/+$/, "")}/api/forecast?shop=${encodeURIComponent(shopId)}`;
  for (let i = 0; i < plan.timeoutsMs.length; i++) {
    if (i > 0) await sleep(plan.backoffMs[i - 1] ?? 0);
    const { forecast, retry } = await attempt(url, fetchImpl, plan.timeoutsMs[i]);
    if (forecast || !retry) return forecast;
  }
  return null;
}
