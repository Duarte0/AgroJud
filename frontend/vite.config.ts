/// <reference types="vitest/config" />
import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";

// The browser talks only to the local Vite origin; /api is proxied to the
// backend, so the API never needs to be reachable from another host.
const LOCAL_HOST = "127.0.0.1";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "AGROJUD_");
  const apiTarget = env.AGROJUD_API_URL ?? "http://127.0.0.1:8000";
  const proxy: Record<string, ProxyOptions> = {
    "/api": { target: apiTarget, changeOrigin: false },
  };

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": path.resolve(import.meta.dirname, "src") },
    },
    server: { host: LOCAL_HOST, port: 5173, strictPort: true, proxy },
    preview: { host: LOCAL_HOST, port: 4173, strictPort: true, proxy },
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.{ts,tsx}"],
      setupFiles: ["src/test/setup.ts"],
      css: false,
      globals: true,
    },
  };
});
