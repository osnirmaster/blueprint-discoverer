import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API is served by `blueprint serve` (FastAPI) on :8765
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { chunkSizeWarningLimit: 4000 },
});
