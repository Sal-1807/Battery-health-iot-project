/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies /api to the FastAPI backend, so the dashboard uses
// relative URLs and needs no CORS in development. Override the target with
// BHM_API_URL (e.g. a backend on another machine).
const apiTarget = process.env.BHM_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { "/api": { target: apiTarget, changeOrigin: true } } },
  preview: { port: 4173, proxy: { "/api": { target: apiTarget, changeOrigin: true } } },
  // recharts alone is ~400 kB minified; it is split into its own cached chunk.
  build: { chunkSizeWarningLimit: 600, rollupOptions: { output: { manualChunks: { charts: ["recharts"] } } } },
  test: { environment: "jsdom", globals: false },
});
