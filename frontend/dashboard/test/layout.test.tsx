import { afterEach, describe, expect, it } from "vitest";
import { act } from "preact/test-utils";
import { cleanup, findAllByText, mount, text } from "./helpers/render";
import { Layout, ROUTES, parseRoute, type Route } from "../src/components/Layout";
import { Header } from "../src/components/Header";
import { resolveAccess } from "../src/lib/access";

afterEach(cleanup);

const demo = resolveAccess({ uid: null, userDoc: null, wantDemo: false });

describe("parseRoute", () => {
  it("defaults to the overview", () => {
    expect(parseRoute("")).toBe("#/");
    expect(parseRoute("#")).toBe("#/");
    expect(parseRoute("#/")).toBe("#/");
    expect(parseRoute("#/nowhere")).toBe("#/");
  });
  it("knows every section route", () => {
    for (const r of ["#/stock", "#/prices", "#/today", "#/orders", "#/audit", "#/forecast", "#/live"]) expect(parseRoute(r)).toBe(r);
    expect(ROUTES.map((r) => r.hash)).toEqual(["#/", "#/today", "#/stock", "#/prices", "#/orders", "#/audit", "#/forecast", "#/live"]);
  });
  it("ignores in-page anchors such as the skip link target", () => {
    expect(parseRoute("#main")).toBeNull();
  });
});

describe("Layout", () => {
  const view = (route: Route = "#/stock") =>
    mount(
      <Layout access={demo} route={route} status="live" updatedText="6:05 pm">
        <p>body</p>
      </Layout>,
    );

  it("has a skip link, the shop name as the only h1, and a labelled section nav", () => {
    const el = view();
    const skip = el.querySelector<HTMLAnchorElement>("a.skip-link")!;
    expect(skip.getAttribute("href")).toBe("#main");
    const h1s = el.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(text(h1s[0])).toContain("Sunrise Dairy");
    const nav = el.querySelector("nav[aria-label='Dashboard sections']")!;
    expect([...nav.querySelectorAll("a")].map((a) => text(a))).toEqual(["Overview", "Today", "Stock", "Prices", "Orders & deliveries", "Audit trail", "Forecast", "Live"]);
    expect(el.querySelector("main#main")?.getAttribute("tabindex")).toBe("-1");
  });

  it("marks the current section with aria-current", () => {
    const el = view("#/stock");
    const current = el.querySelectorAll("nav a[aria-current='page']");
    expect(current).toHaveLength(1);
    expect(text(current[0])).toBe("Stock");
  });

  it("has no demo shop banner", () => {
    const el = view();
    expect(text(el)).not.toMatch(/Demo shop/);
    expect(text(el)).not.toMatch(/read-only/i);
    expect(el.querySelector(".banner")).toBeNull();
  });

  it("uses a grid on the overview and a single column on a section", () => {
    expect(view("#/").querySelector("main")!.className).not.toContain("main--single");
    cleanup();
    expect(view("#/stock").querySelector("main")!.className).toContain("main--single");
  });

  it("record mode (bare) keeps only the main region: no header, banner or tabs", () => {
    const el = mount(
      <Layout access={demo} route="#/live" status="live" bare>
        <p>camera</p>
      </Layout>,
    );
    expect(el.querySelector("header")).toBeNull();
    expect(el.querySelector("nav")).toBeNull();
    expect(el.querySelector(".banner")).toBeNull();
    expect(el.querySelector("main")!.className).toContain("main--record");
    expect(text(el.querySelector("main"))).toBe("camera");
  });

  it("moves focus to the main region when the skip link is used, without changing the route", () => {
    const el = view();
    const before = location.hash;
    act(() => {
      el.querySelector<HTMLAnchorElement>("a.skip-link")!.click();
    });
    expect(document.activeElement).toBe(el.querySelector("main"));
    expect(location.hash).toBe(before);
  });
});

describe("Header", () => {
  it("says Live in words, with the last update time", () => {
    const el = mount(<Header shopName="Sunrise Dairy" status="live" updatedText="6:05 pm" />);
    expect(findAllByText(el, "[role=status]", "Live")).toHaveLength(1);
    expect(text(el)).toContain("Updated 6:05 pm");
    expect(el.querySelector(".live")!.className).toContain("live--live");
  });

  it("says Connecting or Offline when the data is not live", () => {
    const a = mount(<Header shopName="Sunrise Dairy" status="connecting" />);
    expect(findAllByText(a, "[role=status]", "Connecting…")).toHaveLength(1);
    const b = mount(<Header shopName="Sunrise Dairy" status="offline" />);
    expect(findAllByText(b, "[role=status]", "Offline")).toHaveLength(1);
    expect(b.querySelector(".live")!.className).toContain("live--offline");
  });

  it("loads the logo from the Vite base path (served at /dashboard/)", () => {
    const el = mount(<Header shopName="Sunrise Dairy" status="live" />);
    const img = el.querySelector("img")!;
    expect(img.getAttribute("src")).toBe(`${import.meta.env.BASE_URL}aira-logo-192.png`);
  });
});
