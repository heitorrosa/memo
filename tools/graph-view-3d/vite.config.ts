import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // sql.js ships a wasm binary that must be served as an asset; the import below
  // uses ?url so vite emits it next to the bundle (no CDN, fully local).
  assetsInclude: ["**/*.wasm"],
});
