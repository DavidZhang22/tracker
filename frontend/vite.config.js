import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 3000,
    strictPort: true,
    forwardConsole: false,
    fs: { allow: [fileURLToPath(new URL(".", import.meta.url))] },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        proxyTimeout: 240000,
      },
    },
  },
  build: {
    outDir: "build",
    assetsDir: "static",
    target: ["es2020", "chrome87", "edge88", "firefox78", "safari14"],
  },
  test: {
    environment: "jsdom",
    pool: "vmThreads",
    vmMemoryLimit: "256MB",
    globals: true,
    setupFiles: ["./src/setupTests.js"],
    include: ["src/__tests__/**/*.test.{js,jsx}"],
    maxWorkers: 2,
  },
});
