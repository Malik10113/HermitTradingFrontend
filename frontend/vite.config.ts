import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The frontend is served by FastAPI at / in production, so all API calls are
// same-origin. In development Vite proxies /api to the local backend.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["lightweight-charts"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
    // Playwright specs live in e2e/ and need the real browser runtime.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
