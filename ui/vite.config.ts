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
    // vite default: the dashboard is its own artifact (ui/dist). The docker
    // image ships it at /app/ui; `make ui` copies it to src/memo/static/ui.
    outDir: "dist",
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
