import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA and the API share one origin in every deployment: the container
// serves `web/dist` and FastAPI answers `/api/*`. During development Vite
// proxies `/api` to the local server instead of introducing a second origin
// (and with it, cookie and CORS differences the judge would never see).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
