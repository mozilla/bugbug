import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the FastAPI server (server.py) runs on :8000 and serves /api;
// in production it serves the built dist/ itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.API_URL ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
