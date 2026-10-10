// apps/app/test/camera.openurl.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  isAllowedOpenUrl, parseOpenUrl, createOpenUrlHandler, openUrlText, defaultOpen, defaultDial, OPEN_URL_PREFIX,
} from "../src/camera/openUrl";
import { OPEN_URL_PREFIX as CORE_PREFIX } from "../src/core/announce";

describe("OPEN_URL allowlist", () => {
  it.each([
    "https://wa.me/919876543210?text=Order%3A%2030%20x%20Sakthi%20curd%201%20L",
    "https://wa.me/919876543210",
    "https://api.whatsapp.com/send?phone=919876543210&text=hi",
    "tel:+919876543210",
    "tel:04423456789",
  ])("allows %s", (u) => {
    expect(isAllowedOpenUrl(u)).toBe(true);
  });

  it.each([
    "http://wa.me/919876543210",                    // not https
    "https://wa.me.evil.com/919876543210",          // look-alike host
    "https://evil.com/?u=https://wa.me/91",         // allowed URL only in the query
    "https://user:pw@wa.me/91",                     // credentials
    "https://wa.me:8443/91",                        // non-default port
    "https://api.whatsapp.com.evil.com/send",
    "https://web.whatsapp.com/send?phone=91",       // not on the list
    "https://wa.me@evil.com/91",                    // wa.me as a username
    "javascript:alert(1)",
    "intent://wa.me/#Intent;scheme=https;end",
    "data:text/html,hi",
    "tel:",
    "tel:+91<script>",
    "tel:+91;phone-context=evil",
    "https://wa.me/91 https://evil.com",            // whitespace
    "",
  ])("rejects %s", (u) => {
    expect(isAllowedOpenUrl(u)).toBe(false);
  });
});

describe("parseOpenUrl", () => {
  it("reads a WhatsApp click-to-chat link", () => {
    expect(parseOpenUrl("OPEN_URL https://wa.me/919876543210?text=hi")).toEqual({
      url: "https://wa.me/919876543210?text=hi", kind: "whatsapp",
    });
    expect(parseOpenUrl("OPEN_URL https://api.whatsapp.com/send?phone=91&text=hi")?.kind).toBe("whatsapp");
  });
  it("reads a tel: link as a call", () => {
    expect(parseOpenUrl("OPEN_URL tel:+919876543210")).toEqual({ url: "tel:+919876543210", kind: "call" });
  });
  it("ignores ordinary summaries, the wrong prefix case and disallowed URLs", () => {
    expect(parseOpenUrl("Order sent to Sakthi vendor")).toBeNull();
    expect(parseOpenUrl("open_url https://wa.me/91")).toBeNull();
    expect(parseOpenUrl("OPEN_URL https://evil.com/")).toBeNull();
    expect(parseOpenUrl("OPEN_URL ")).toBeNull();
  });
  it("takes only the first token after the prefix", () => {
    expect(parseOpenUrl("OPEN_URL  https://wa.me/91?text=a  (vendor)")?.url).toBe("https://wa.me/91?text=a");
  });
});

function setup(openResult = true) {
  const log: string[] = [];
  const h = createOpenUrlHandler({
    speak: (s) => log.push(`say:${s}`),
    lang: () => "en",
    open: (u) => { log.push(`open:${u}`); return openResult; },
    dial: (u) => { log.push(`dial:${u}`); return true; },
  });
  return { h, log };
}
const state = (summary: string, id = "o1") => ({ t: "state" as const, entity: "order" as const, id, state: "ORDER_PLACED", summary });

describe("OPEN_URL handler", () => {
  it("speaks first, then opens WhatsApp in a new tab", () => {
    const s = setup();
    expect(s.h.onServerMsg(state("OPEN_URL https://wa.me/919876543210?text=hi"))).toBe(true);
    expect(s.log).toEqual(["say:Opening WhatsApp.", "open:https://wa.me/919876543210?text=hi"]);
  });
  it("dials tel: links instead of opening a tab", () => {
    const s = setup();
    s.h.onServerMsg(state("OPEN_URL tel:+919876543210"));
    expect(s.log).toEqual(["say:Calling the vendor.", "dial:tel:+919876543210"]);
  });
  it("never opens a disallowed link and says nothing", () => {
    const s = setup();
    expect(s.h.onServerMsg(state("OPEN_URL https://evil.example/pay"))).toBe(false);
    expect(s.h.onServerMsg(state("OPEN_URL javascript:alert(1)"))).toBe(false);
    expect(s.log).toEqual([]);
  });
  it("ignores other messages and ordinary state summaries", () => {
    const s = setup();
    expect(s.h.onServerMsg({ t: "cue", cue: "ack" })).toBe(false);
    expect(s.h.onServerMsg(state("Order sent to Sakthi vendor"))).toBe(false);
    expect(s.log).toEqual([]);
  });
  it("opens the same link for the same entity only once (resent state)", () => {
    const s = setup();
    s.h.onServerMsg(state("OPEN_URL https://wa.me/91?text=a"));
    s.h.onServerMsg(state("OPEN_URL https://wa.me/91?text=a"));
    s.h.onServerMsg(state("OPEN_URL https://wa.me/91?text=a", "o2"));
    expect(s.log.filter((l) => l.startsWith("open:"))).toHaveLength(2);
  });
  it("when the browser blocks the tab, says so and keeps the link for a later user tap", () => {
    const s = setup(false);
    s.h.onServerMsg(state("OPEN_URL https://wa.me/91?text=a"));
    expect(s.log).toEqual([
      "say:Opening WhatsApp.", "open:https://wa.me/91?text=a", "say:Could not open the link. Double-tap to open it.",
    ]);
    expect(s.h.pending()).toBe("https://wa.me/91?text=a");
    s.log.length = 0;
    expect(s.h.openPending()).toBe(false); // still blocked
    expect(s.h.pending()).toBe("https://wa.me/91?text=a");
  });
  it("openPending opens the kept link once it succeeds, then clears it", () => {
    let ok = false; const opened: string[] = [];
    const h = createOpenUrlHandler({ speak: () => {}, lang: () => "en", open: (u) => { opened.push(u); return ok; }, dial: () => true });
    h.onServerMsg(state("OPEN_URL https://wa.me/91"));
    ok = true;
    expect(h.openPending()).toBe(true);
    expect(h.pending()).toBeNull();
    expect(h.openPending()).toBe(false);
    expect(opened).toEqual(["https://wa.me/91", "https://wa.me/91"]);
  });
  it("has every phrase in en, ta and hi, and speaks in the owner's language", () => {
    for (const lang of ["en", "ta", "hi"] as const) {
      for (const k of ["whatsapp", "call", "blocked"] as const) expect(openUrlText(k, lang).length).toBeGreaterThan(0);
    }
    expect(openUrlText("whatsapp", "ta")).not.toBe(openUrlText("whatsapp", "en"));
    const said: string[] = [];
    const h = createOpenUrlHandler({ speak: (s) => said.push(s), lang: () => "hi", open: () => true, dial: () => true });
    h.onServerMsg(state("OPEN_URL tel:+919876543210"));
    expect(said).toEqual([openUrlText("call", "hi")]);
  });
  it("uses the same prefix the announcer keeps silent", () => {
    expect(OPEN_URL_PREFIX).toBe(CORE_PREFIX);
  });
});

describe("browser defaults", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("defaultOpen opens a detached new tab and reports a blocked popup", () => {
    const tab = { opener: {} as unknown };
    const open = vi.fn(() => tab);
    vi.stubGlobal("open", open);
    expect(defaultOpen("https://wa.me/91")).toBe(true);
    expect(open).toHaveBeenCalledWith("https://wa.me/91", "_blank");
    expect(tab.opener).toBeNull();
    open.mockReturnValueOnce(null as unknown as typeof tab);
    expect(defaultOpen("https://wa.me/91")).toBe(false);
  });

  it("defaultDial hands a tel: link to the dialler through location.href", () => {
    const loc = { href: "https://aira.example/" };
    vi.stubGlobal("location", loc);
    expect(defaultDial("tel:+919876543210")).toBe(true);
    expect(loc.href).toBe("tel:+919876543210");
  });
});
