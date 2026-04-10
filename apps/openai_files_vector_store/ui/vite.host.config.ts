import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

const input = process.env.INPUT;
if (!input) {
  throw new Error("INPUT environment variable is not set");
}

export default defineConfig({
  plugins: [
    react(),
    viteSingleFile({
      useRecommendedBuildConfig: false,
    }),
  ],
  base: "./",
  build: {
    assetsDir: "",
    assetsInlineLimit: () => true,
    chunkSizeWarningLimit: 100000000,
    cssCodeSplit: false,
    outDir: "host-dist",
    emptyOutDir: false,
    sourcemap: "inline",
    minify: false,
    cssMinify: false,
    rollupOptions: {
      input,
      output: {
        codeSplitting: false,
      },
    },
  },
});
