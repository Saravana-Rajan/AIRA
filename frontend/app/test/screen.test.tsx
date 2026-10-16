import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { Screen, SURFACE_LABEL } from "../src/ui/Screen";
import { initialScreen, type ScreenState } from "../src/ui/screenState";

let root: HTMLElement;
const noop = () => {};

function mount(state: ScreenState, level = 0) {
  act(() => { render(<Screen state={state} level={level} shopName="Sunrise Dairy" onTalk={noop} onStop={noop} />, root); });
}
const q = (sel: string) => root.querySelector(sel);

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
});
afterEach(() => {
  act(() => render(null, root));
  root.remove();
  vi.useRealTimers();
});

describe("Screen", () => {
  it("is a single full-screen gesture surface with the spoken label", () => {
    mount(initialScreen);
    expect(SURFACE_LABEL).toBe("AIRA. Double-tap to talk. Long-press to stop.");
    const buttons = root.querySelectorAll("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("aria-label")).toBe(SURFACE_LABEL);
    // nothing small to tap: no links, inputs or extra focus stops inside the surface
    expect(root.querySelectorAll("a, input, select, textarea, [tabindex]")).toHaveLength(0);
  });

  it("shows the small AIRA logo in the top bar, without a shop-name subheading", () => {
    mount(initialScreen);
    const logo = q(".topbar img") as HTMLImageElement;
    expect(logo.getAttribute("src")).toBe("/icons/aira-logo-192.png");
    expect(q(".brand__shop")).toBeNull();                          // the shop subheading was removed
    expect(q(".topbar")!.textContent).not.toContain("Sunrise Dairy");
  });

  it("status chip shows the label and colours by state", () => {
    mount({ ...initialScreen, status: { kind: "counting", label: "Counting 12 / 30" } });
    expect(q(".status-chip")!.textContent).toBe("Counting 12 / 30");
    expect(q(".screen")!.getAttribute("data-status")).toBe("counting");
  });

  it("shows a gesture hint before anything is said", () => {
    mount(initialScreen);
    expect(q(".captions")!.textContent).toContain("Double-tap anywhere to talk.");
    expect(q(".caption--user")).toBeNull();
  });

  it("shows the last thing the user said and the last thing AIRA said, each under its speaker label", () => {
    mount({ ...initialScreen, userText: "2 litres curd", airaText: "Sakthi curd 1 L, take one more." });
    expect(q(".caption--user .caption__who")!.textContent).toBe("You");
    expect(q(".caption--user .caption__text")!.textContent).toBe("2 litres curd");
    expect(q(".caption--aira .caption__who")!.textContent).toBe("AIRA");
    expect(q(".caption--aira .caption__text")!.textContent).toBe("Sakthi curd 1 L, take one more.");
  });

  it("the gesture hint shows exactly once: in the empty caption card first, at the bottom once talking starts", () => {
    mount(initialScreen);
    expect(q(".captions")!.classList.contains("captions--empty")).toBe(true);
    expect(q(".hint")).toBeNull();
    mount({ ...initialScreen, userText: "hello", airaText: "Hi." });
    expect(q(".captions")!.classList.contains("captions--empty")).toBe(false);
    expect(q(".caption--hint")).toBeNull();
    expect(q(".hint")!.textContent).toBe("Double-tap anywhere to talk. Long-press to stop.");
    expect(root.querySelectorAll(".hint, .caption--hint")).toHaveLength(1);
  });

  it("lays the screen out as one phone column: top bar, orb stage, caption card, task card, hint", () => {
    mount({ ...initialScreen, userText: "2 litres curd", airaText: "120 rupees.", task: { title: "Sale", detail: "2 × Sakthi curd 1 L — ₹120", tone: "ok" } });
    const screen = q("button.surface > .screen")!;
    expect(screen).not.toBeNull();
    const order = [...screen.children].map((el) => el.className.split(" ")[0]);
    expect(order).toEqual(["topbar", "stage", "captions", "task-card", "hint"]);
    expect(screen.querySelector(".topbar .brand__logo")).not.toBeNull();
    expect(screen.querySelector(".topbar .status-chip")).not.toBeNull();
    expect(screen.querySelector(".stage .orb")).not.toBeNull();
  });

  it("task card appears only when there is a task, with its tone", () => {
    mount(initialScreen);
    expect(q(".task-card")).toBeNull();
    mount({ ...initialScreen, task: { title: "Delivery", detail: "28 / 30 counted", tone: "warn" } });
    const card = q(".task-card")!;
    expect(card.getAttribute("data-tone")).toBe("warn");
    expect(card.textContent).toContain("Delivery");
    expect(card.textContent).toContain("28 / 30 counted");
  });

  it("voice orb is decorative and follows the audio level", () => {
    mount(initialScreen, 0.5);
    const orb = q(".orb") as HTMLElement;
    expect(orb.getAttribute("aria-hidden")).toBe("true");
    expect(orb.style.getPropertyValue("--level")).toBe("0.5");
  });

  it("clamps the orb level to 0..1", () => {
    mount(initialScreen, 3);
    expect((q(".orb") as HTMLElement).style.getPropertyValue("--level")).toBe("1");
  });
});
