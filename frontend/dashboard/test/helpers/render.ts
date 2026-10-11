// Minimal Preact test rendering (no extra deps): mounts into jsdom and flushes effects with act().
import { render, type ComponentChild } from "preact";
import { act } from "preact/test-utils";

const mounted: HTMLElement[] = [];

export function mount(vnode: ComponentChild): HTMLElement {
  const el = document.createElement("div");
  document.body.appendChild(el);
  mounted.push(el);
  act(() => {
    render(vnode, el);
  });
  return el;
}

export function cleanup(): void {
  for (const el of mounted.splice(0)) {
    act(() => {
      render(null, el);
    });
    el.remove();
  }
}

/** Waits for pending promises (and the effects they trigger) to settle. */
export async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

/** Visible text of an element with whitespace collapsed. */
export function text(el: Element | null | undefined): string {
  return (el?.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** All elements matching the selector whose collapsed text equals (or matches) the given text. */
export function findAllByText(root: ParentNode, selector: string, want: string | RegExp): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(selector)].filter((e) =>
    typeof want === "string" ? text(e) === want : want.test(text(e)),
  );
}
