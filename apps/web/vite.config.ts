import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

function loopback(name: string, fallback: string): string {
  const value = process.env[name] ?? fallback;
  if (value !== "127.0.0.1") {
    throw new Error(`${name} must be the fixed loopback host`);
  }
  return value;
}

function port(name: string, fallback: number): number {
  const raw = process.env[name] ?? String(fallback);
  if (!/^[1-9][0-9]*$/u.test(raw)) {
    throw new Error(`${name} must be a canonical port`);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value > 65_535) {
    throw new Error(`${name} must be a canonical port`);
  }
  return value;
}

const apiHost = loopback("ATHENA_API_HOST", "127.0.0.1");
const apiPort = port("ATHENA_API_PORT", 8000);
const webHost = loopback("ATHENA_WEB_HOST", "127.0.0.1");
const webPort = port("ATHENA_WEB_PORT", 5173);
const apiProxy = {
  target: `http://${apiHost}:${String(apiPort)}`,
  changeOrigin: false,
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: webHost,
    port: webPort,
    strictPort: true,
    proxy: {
      "/health": apiProxy,
      "/v1": apiProxy,
    },
  },
});
