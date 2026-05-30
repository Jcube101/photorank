import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The PWA ships from frontend/. The service worker and manifest live in
// public/ so they are copied to the build root unhashed — a service worker
// must be served from a stable path at the site root to control the scope.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
