// jsdom has no `onpointer*` handler properties, so Preact would register `onPointerDown` as a
// case-sensitive "PointerDown" listener. Declaring them makes Preact use the real DOM event names.
for (const name of ["onpointerdown", "onpointerup", "onpointercancel", "onpointerleave", "onpointermove"]) {
  if (!(name in HTMLElement.prototype)) {
    Object.defineProperty(HTMLElement.prototype, name, { value: null, writable: true, configurable: true });
  }
}
