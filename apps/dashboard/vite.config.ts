import { defineConfig } from "vitest/config";
import preact from "@preact/preset-vite";

// Served from the single Hosting site aira-shop at /dashboard/ (see scripts/build_hosting.sh).
export default defineConfig({
  base: "/dashboard/",
  plugins: [preact()],
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.ts?(x)"],
    // Process CSS so `?raw` imports return the real text (the theme tests read tokens.css).
    css: { include: [/.+/] },
  },
});
