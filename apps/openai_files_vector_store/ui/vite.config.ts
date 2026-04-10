import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

const serveMcpAppAtRoot = (): Plugin => ({
  name: "serve-mcp-app-at-root",
  configureServer(server) {
    server.middlewares.use((request, _response, next) => {
      if (request.url === "/") {
        request.url = "/mcp-app.html";
      }

      next();
    });
  },
});

export default defineConfig({
  plugins: [
    serveMcpAppAtRoot(),
    react(),
    viteSingleFile({
      useRecommendedBuildConfig: false,
    }),
  ],
  base: "./",
  server: {
    host: "0.0.0.0",
    port: 5174,
    strictPort: true,
  },
  build: {
    assetsDir: "",
    assetsInlineLimit: () => true,
    chunkSizeWarningLimit: 100000000,
    cssCodeSplit: false,
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: "inline",
    minify: false,
    cssMinify: false,
    rollupOptions: {
      input: "mcp-app.html",
      output: {
        // Match the single-file plugin's intent without using the deprecated
        // `inlineDynamicImports` option that now warns under Rolldown.
        codeSplitting: false,
      },
    },
  },
});
