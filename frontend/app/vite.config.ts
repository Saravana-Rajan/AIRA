import { defineConfig } from "vitest/config";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.ts?(x)"],
    setupFiles: ["test/setup.ts"],
    // Process CSS so `?raw` imports return the real text (the theme tests read tokens.css).
    css: { include: [/.+/] },
  },
});
