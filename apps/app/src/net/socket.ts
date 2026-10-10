import { parseServerMsg, type ClientMsg, type ServerMsg } from "@aira/contracts";

/** Reconnect delay: 500 → 1000 → 2000 → 4000 → 8000 → 15000 ms (maximum). */
export const backoffMs = (attempt: number): number => Math.min(500 * 2 ** attempt, 15000);

type Hello = Extract<ClientMsg, { t: "hello" }>;
type Handler = (m: ServerMsg) => void;

/**
 * The app's single WebSocket to aira-live. Sends `hello` on every (re)connect, forwards only
 * messages that pass the contract guard (unknown or future types are dropped), and reconnects
 * with backoff until `close()` is called.
 */
export class AiraSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private attempt = 0;
  private hello: Hello | null = null;
  private closedByUser = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly WS: typeof WebSocket;

  constructor(private readonly url: string, opts: { WebSocketImpl?: typeof WebSocket } = {}) {
    this.WS = opts.WebSocketImpl ?? WebSocket;
  }

  connect(hello: Hello): void {
    this.hello = hello;
    this.closedByUser = false;
    this.attempt = 0;
    this.open();
  }

  send(msg: ClientMsg): boolean {
    if (!this.ws || this.ws.readyState !== 1) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  on(h: Handler): () => void {
    this.handlers.add(h);
    return () => {
      this.handlers.delete(h);
    };
  }

  close(): void {
    this.closedByUser = true;
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.ws?.close();
  }

  private open(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const ws = new this.WS(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      ws.send(JSON.stringify(this.hello));
    };
    ws.onmessage = (e: MessageEvent | { data: string }) => {
      const m = parseServerMsg(String(e.data));
      if (m) this.handlers.forEach((h) => h(m));
    };
    ws.onclose = () => {
      if (this.ws !== ws || this.closedByUser || !this.hello) return;
      this.ws = null;
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        if (!this.closedByUser) this.open();
      }, backoffMs(this.attempt++));
    };
  }
}
