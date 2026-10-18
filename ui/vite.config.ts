import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Fake origin for the dev/preview proxies: the memo server mounts /api at the
// root and serves this bundle under /ui/.
const server = "http://localhost:5000";

export default defineConfig({
  base: "/ui/",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/memo/static/ui",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: { "/api": server, "/health": server },
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy: { "/api": server, "/health": server },
  },
});
