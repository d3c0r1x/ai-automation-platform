import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке дашборд и API живут на разных портах, поэтому запросы к /api
// проксируются на FastAPI. В продакшене сборка отдаётся тем же приложением
// (StaticFiles), и прокси не нужен.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
