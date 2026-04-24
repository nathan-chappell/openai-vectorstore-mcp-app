import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const uiDir = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: uiDir,
  envDir: resolve(uiDir, ".."),
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: resolve(uiDir, "dist"),
    emptyOutDir: true,
    sourcemap: true,
    minify: false,
  },
});
