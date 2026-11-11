import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      "^/api/(?:lca/|projects/[^/]+/lca(?:/|$))": {
        target: `http://127.0.0.1:${process.env.TAPPER_API_PORT ?? "8000"}`,
        changeOrigin: false,
      },
      "/api": process.env.TAP_WEB_API_TARGET ?? "http://127.0.0.1:8001",
    },
  },
});
