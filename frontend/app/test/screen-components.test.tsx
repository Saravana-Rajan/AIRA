import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { StatusChip } from "../src/ui/StatusChip";
import { CaptionArea } from "../src/ui/CaptionArea";
import { TaskCard } from "../src/ui/TaskCard";
import { ScreenReaderRegion, announceToRegion } from "../src/ui/ScreenReaderRegion";
import { Screen } from "../src/ui/Screen";
import { initialScreen } from "../src/ui/screenState";
import { captionTail, mergeCaption, progressOf, statusLabel } from "../src/ui/view";

let root: HTMLElement;
beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); });
afterEach(() => { act(() => render(null, root)); root.remove(); vi.useRealTimers(); });

/** Everything inside the GestureSurface <button> must be phrasing content. */
const PHRASING_ONLY = (el: Element) =>
  [...el.querySelectorAll("*")].every((n) => ["SPAN", "SVG", "PATH", "IMG", "CIRCLE"].includes(n.tagName.toUpperCase()));

describe("view helpers (pure)", () => {
  it("statusLabel localises every chip state and appends the live count", () => {
    expect(statusLabel("ready", "en")).toBe("Ready");
    expect(statusLabel("listening", "ta")).toBe("கேட்கிறேன்");
    expect(statusLabel("counting", "en", { counted: 12, expected: 30 })).toBe("Counting 12 / 30");
    expect(statusLabel("counting", "hi", { counted: 7, expected: null })).toBe("गिन रहा हूँ 7");
    expect(statusLabel("stop", "en")).toBe("Stop!");
    expect(statusLabel("offline", "en")).toBe("Offline");
  });
  it("progressOf clamps to 0..1 and is null without an expected total", () => {
    expect(progressOf(28, 30)).toBeCloseTo(28 / 30, 5);
    expect(progressOf(35, 30)).toBe(1);
    expect(progressOf(3, null)).toBeNull();
    expect(progressOf(0, 0)).toBeNull();
  });
  it("mergeCaption appends streamed deltas, accepts cumulative partials, and lets a full final replace", () => {
    let c = mergeCaption({ text: "", final: true }, { text: "Two Sakthi", final: false });
    c = mergeCaption(c, { text: " curd", final: false });
    expect(c.text).toBe("Two Sakthi curd");
    c = mergeCaption(c, { text: "Two Sakthi curd, 120", final: false }); // cumulative partial
    expect(c.text).toBe("Two Sakthi curd, 120");
    c = mergeCaption(c, { text: "Two Sakthi curd, 120 rupees.", final: true });
    expect(c).toEqual({ text: "Two Sakthi curd, 120 rupees.", final: true });
    c = mergeCaption(c, { text: "Next", final: false }); // a new turn starts fresh
    expect(c.text).toBe("Next");
  });
  it("mergeCaption appends a final that is only the last fragment, but takes a corrected full final", () => {
    const partial = { text: "Two Sakthi curd, 120", final: false };
    expect(mergeCaption(partial, { text: " rupees.", final: true })).toEqual({ text: "Two Sakthi curd, 120 rupees.", final: true });
    expect(mergeCaption(partial, { text: "Two Shakti curd, 120 rupees.", final: true }).text).toBe("Two Shakti curd, 120 rupees.");
    expect(mergeCaption({ text: "", final: true }, { text: "Done.", final: true })).toEqual({ text: "Done.", final: true });
  });
  it("captionTail keeps the end of long text on a word boundary", () => {
    const t = captionTail("one two three four five six", 12);
    expect(t.startsWith("…")).toBe(true);
    expect(t.endsWith("five six")).toBe(true);
    expect(t.length).toBeLessThanOrEqual(13);
    expect(captionTail("short", 12)).toBe("short");
  });
});

describe("StatusChip", () => {
  it("renders the label with its state as a data attribute, phrasing content only", () => {
    render(<StatusChip kind="listening" label="Listening" />, root);
    const chip = root.querySelector(".status-chip")!;
    expect(chip.getAttribute("data-kind")).toBe("listening");
    expect(chip.textContent).toBe("Listening");
    expect(chip.querySelector(".status-chip__dot")!.getAttribute("aria-hidden")).toBe("true");
    expect(PHRASING_ONLY(root)).toBe(true);
  });
});

describe("CaptionArea", () => {
  it("shows a localised hint when nothing has been said yet", () => {
    render(<CaptionArea userText="" airaText="" lang="hi" />, root);
    expect(root.querySelector(".caption--hint")!.textContent).toContain("दो बार");
    expect(root.querySelector(".caption--user")).toBeNull();
  });
  it("is one card with a You row and an AIRA row: a localised speaker label over each text line", () => {
    render(<CaptionArea userText="Two Sakthi curd" airaText="That is 120 rupees." lang="ta" />, root);
    const card = root.querySelector(".captions")!;
    expect(card.classList.contains("captions--empty")).toBe(false);
    const rows = card.querySelectorAll(":scope > .caption");
    expect([...rows].map((r) => r.className)).toEqual(["caption caption--user", "caption caption--aira"]);
    const user = rows[0];
    const aira = rows[1];
    expect(user.querySelector(".caption__who")!.textContent).toBe("நீங்கள்");
    expect(user.querySelector(".caption__text")!.textContent).toBe("Two Sakthi curd");
    expect(aira.querySelector(".caption__who")!.textContent).toBe("AIRA");
    expect(aira.querySelector(".caption__text")!.textContent).toBe("That is 120 rupees.");
    expect(root.querySelector(".caption--hint")).toBeNull();
    expect(PHRASING_ONLY(root)).toBe(true);
  });
  it("shows only the row that has words", () => {
    render(<CaptionArea userText="" airaText="Ready." lang="en" />, root);
    expect(root.querySelector(".caption--user")).toBeNull();
    expect(root.querySelector(".caption--aira .caption__who")!.textContent).toBe("AIRA");
  });
  it("keeps the newest words of a long caption", () => {
    const long = "Sakthi curd one litre, ".repeat(10) + "and one Aavin milk.";
    render(<CaptionArea userText="" airaText={long} lang="en" max={60} />, root);
    const text = root.querySelector(".caption--aira .caption__text")!.textContent!;
    expect(text.startsWith("…")).toBe(true);
    expect(text.endsWith("and one Aavin milk.")).toBe(true);
  });
});

describe("TaskCard", () => {
  it("renders nothing when there is no task", () => {
    render(<TaskCard task={null} lang="en" />, root);
    expect(root.innerHTML).toBe("");
  });
  it("shows title, detail, tone and a shape mark, and a progress bar for a live count", () => {
    render(<TaskCard lang="en" task={{ title: "Delivery", detail: "28 / 30 counted", tone: "neutral" }} progress={{ counted: 28, expected: 30 }} />, root);
    const card = root.querySelector(".task-card")!;
    expect(card.getAttribute("data-tone")).toBe("neutral");
    expect(card.querySelector(".task-card__title")!.textContent).toBe("Delivery");
    expect(card.querySelector(".task-card__detail")!.textContent).toBe("28 / 30 counted");
    expect(card.querySelector(".task-card__icon")!.getAttribute("aria-hidden")).toBe("true");
    const fill = root.querySelector<HTMLElement>(".task-card__fill")!;
    expect(fill.style.getPropertyValue("--progress")).toBe("0.933");
    expect(PHRASING_ONLY(root)).toBe(true);
  });
  it("draws a different mark for each tone so state is not shown by colour alone", () => {
    const marks = (["neutral", "ok", "warn", "danger"] as const).map((tone) => {
      render(<TaskCard lang="en" task={{ title: "Order", detail: "x", tone }} />, root);
      return root.querySelector(".task-card__icon path")!.getAttribute("d");
    });
    expect(new Set(marks).size).toBe(4);
  });
  it("shows the localised yes/no gesture hint while a confirmation is pending", () => {
    render(<TaskCard lang="en" confirmPending task={{ title: "Confirm order", detail: "Order 30 litres Sakthi curd?", tone: "neutral" }} />, root);
    expect(root.querySelector(".task-card__hint")!.textContent).toBe("Double-tap for yes, long-press for no.");
    expect(root.querySelector(".task-card")!.getAttribute("data-confirm")).toBe("true");
    render(<TaskCard lang="ta" confirmPending task={{ title: "Confirm", detail: "?", tone: "neutral" }} />, root);
    expect(root.querySelector(".task-card__hint")!.textContent).toContain("இருமுறை");
  });
  it("has no hint and no bar when idle", () => {
    render(<TaskCard lang="en" task={{ title: "Sale", detail: "2 × Sakthi curd 1 L — ₹120", tone: "ok" }} />, root);
    expect(root.querySelector(".task-card__hint")).toBeNull();
    expect(root.querySelector(".task-card__bar")).toBeNull();
    expect(root.querySelector(".task-card")!.hasAttribute("data-confirm")).toBe(false);
  });
});

describe("Screen uses the components", () => {
  const noop = () => {};
  it("localises the hint, and passes progress and the pending confirm through to the task card", () => {
    act(() => {
      render(
        <Screen
          state={{ ...initialScreen, status: { kind: "counting", label: "Counting 28 / 30" }, task: { title: "Delivery", detail: "28 / 30 counted", tone: "neutral" } }}
          lang="ta"
          progress={{ counted: 28, expected: 30 }}
          confirmPending
          onTalk={noop}
          onStop={noop}
        />,
        root,
      );
    });
    expect(root.querySelector(".caption--hint")!.textContent).toContain("இருமுறை");
    expect(root.querySelector(".status-chip")!.getAttribute("data-kind")).toBe("counting");
    expect(root.querySelector(".task-card__fill")).not.toBeNull();
    expect(root.querySelector(".task-card__hint")).not.toBeNull();
    expect(root.querySelectorAll("button")).toHaveLength(1);
    expect(PHRASING_ONLY(root.querySelector("button")!)).toBe(true);
  });
});

describe("ScreenReaderRegion", () => {
  it("is one polite live region that re-announces even identical text", () => {
    vi.useFakeTimers();
    act(() => { render(<ScreenReaderRegion />, root); });
    const region = root.querySelector('[aria-live="polite"]')!;
    expect(region.getAttribute("role")).toBe("status");
    expect(root.querySelectorAll("[aria-live]")).toHaveLength(1);
    act(() => { announceToRegion("Order 30 litres?"); });
    act(() => { vi.advanceTimersByTime(50); });
    expect(region.textContent).toBe("Order 30 litres?");
    act(() => { announceToRegion("Order 30 litres?"); });
    expect(region.textContent).toBe(""); // cleared first so TalkBack reads the repeat
    act(() => { vi.advanceTimersByTime(50); });
    expect(region.textContent).toBe("Order 30 litres?");
  });
  it("delivers an announcement made before the region mounted", () => {
    vi.useFakeTimers();
    announceToRegion("Offline. Sales are saved and will sync.");
    act(() => { render(<ScreenReaderRegion />, root); });
    act(() => { vi.advanceTimersByTime(50); });
    expect(root.querySelector('[aria-live="polite"]')!.textContent).toBe("Offline. Sales are saved and will sync.");
  });
  it("an older region unmounting does not silence a newer one", () => {
    vi.useFakeTimers();
    const other = document.createElement("div");
    document.body.appendChild(other);
    act(() => { render(<ScreenReaderRegion />, other); });
    act(() => { render(<ScreenReaderRegion />, root); });
    act(() => { render(null, other); });
    other.remove();
    act(() => { announceToRegion("Back online."); });
    act(() => { vi.advanceTimersByTime(50); });
    expect(root.querySelector('[aria-live="polite"]')!.textContent).toBe("Back online.");
  });
});
