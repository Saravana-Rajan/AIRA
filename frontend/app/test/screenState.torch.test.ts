// frontend/app/test/screenState.torch.test.ts — the flashlight on the status chip: only the phone's own report.
import { describe, it, expect } from "vitest";
import { reduceScreen, initialScreen } from "../src/ui/screenState";
import { localizeStatus } from "../src/ui/AppShell";
import { LABELS, LIGHT } from "../src/i18n/strings";

describe("torch on the chip", () => {
  it("local torch events set the torch state without touching the status", () => {
    const ready = reduceScreen(initialScreen, { t: "ready", sessionId: "s", resume: "s", mock: false });
    const on = reduceScreen(ready, { t: "local", event: "torch_on" });
    expect(on.torch).toBe("on");
    expect(on.status).toEqual(ready.status);
    expect(reduceScreen(on, { t: "local", event: "torch_off" }).torch).toBe("off");
    expect(reduceScreen(on, { t: "local", event: "torch_unsupported" }).torch).toBe("unsupported");
    // a server torch command on its own changes nothing on screen: the chip waits for the phone's report
    expect(reduceScreen(ready, { t: "torch", on: true }).torch).toBeUndefined();
  });

  it("the chip says 'Flashlight on' in the owner's language only while it is on", () => {
    const ready = reduceScreen(initialScreen, { t: "ready", sessionId: "s", resume: "s", mock: false });
    expect(localizeStatus(ready, "ta", null).status.label).toBe("தயார்");
    const on = reduceScreen(ready, { t: "local", event: "torch_on" });
    expect(localizeStatus(on, "en", null).status.label).toBe("Ready · Flashlight on");
    expect(localizeStatus(on, "ta", null).status.label).toBe(`தயார் · ${LABELS.torchOn.ta}`);
    expect(localizeStatus(on, "hi", null).status.label).toBe(`तैयार · ${LABELS.torchOn.hi}`);
    const unsupported = reduceScreen(ready, { t: "local", event: "torch_unsupported" });
    expect(localizeStatus(unsupported, "en", null).status.label).toBe("Ready");
  });

  it("every low-light line exists in ta, hi and en with the same placeholders", () => {
    for (const row of Object.values(LIGHT)) {
      expect(Object.keys(row).sort()).toEqual(["en", "hi", "ta"]);
      const fields = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
      expect(fields(row.ta)).toEqual(fields(row.en));
      expect(fields(row.hi)).toEqual(fields(row.en));
    }
    expect(LIGHT.torchUnsupported.en).toBe("I can't turn on the flashlight on this phone.");
  });
});
