import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { GestureSurface } from "../src/ui/GestureSurface";

const LABEL = "AIRA. Double-tap to talk. Long-press to stop.";
let root: HTMLElement;
let onTalk: ReturnType<typeof vi.fn>;
let onStop: ReturnType<typeof vi.fn>;

function mount() {
  act(() => {
    render(
      <GestureSurface label={LABEL} onTalk={onTalk} onStop={onStop}>
        <span data-testid="inside">inside</span>
      </GestureSurface>,
      root,
    );
  });
  return root.querySelector("button")!;
}
const fire = (el: Element, type: string) => act(() => { el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true })); });
const tap = (el: Element) => { fire(el, "pointerdown"); fire(el, "pointerup"); };

beforeEach(() => {
  vi.useFakeTimers();
  root = document.createElement("div");
  document.body.appendChild(root);
  onTalk = vi.fn();
  onStop = vi.fn();
});
afterEach(() => {
  act(() => render(null, root));
  root.remove();
  vi.useRealTimers();
});

describe("GestureSurface", () => {
  it("is one full-screen button with the spoken label and renders its children", () => {
    const btn = mount();
    expect(root.querySelectorAll("button")).toHaveLength(1);
    expect(btn.getAttribute("aria-label")).toBe(LABEL);
    expect(btn.querySelector("[data-testid=inside]")).not.toBeNull();
  });

  it("double-tap within 350 ms talks once", () => {
    const btn = mount();
    tap(btn);
    vi.advanceTimersByTime(200);
    tap(btn);
    expect(onTalk).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(100);
    tap(btn); // a third tap starts a new pair; it does not talk again
    expect(onTalk).toHaveBeenCalledTimes(1);
    expect(onStop).not.toHaveBeenCalled();
  });

  it("two taps 400 ms apart do not talk", () => {
    const btn = mount();
    tap(btn);
    vi.advanceTimersByTime(400);
    tap(btn);
    expect(onTalk).not.toHaveBeenCalled();
  });

  it("long-press of 600 ms stops, and the release is not a tap", () => {
    const btn = mount();
    tap(btn);
    vi.advanceTimersByTime(100);
    fire(btn, "pointerdown");
    vi.advanceTimersByTime(599);
    expect(onStop).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onStop).toHaveBeenCalledTimes(1);
    fire(btn, "pointerup");
    expect(onTalk).not.toHaveBeenCalled();
  });

  it("pointercancel cancels a long-press", () => {
    const btn = mount();
    fire(btn, "pointerdown");
    vi.advanceTimersByTime(300);
    fire(btn, "pointercancel");
    vi.advanceTimersByTime(1000);
    expect(onStop).not.toHaveBeenCalled();
  });

  it("keyboard or screen-reader activation (click with detail 0) talks; pointer clicks do not", () => {
    const btn = mount();
    act(() => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 })); });
    expect(onTalk).toHaveBeenCalledTimes(1);
    act(() => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 })); });
    expect(onTalk).toHaveBeenCalledTimes(1);
  });

  it("Escape stops", () => {
    const btn = mount();
    act(() => { btn.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("suppresses the long-press context menu", () => {
    const btn = mount();
    const ev = new Event("contextmenu", { bubbles: true, cancelable: true });
    act(() => { btn.dispatchEvent(ev); });
    expect(ev.defaultPrevented).toBe(true);
  });
});
