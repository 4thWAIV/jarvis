import { defineConfig, searchForWorkspaceRoot } from "vite";
import { resolve } from "path";

// The same headers server.py's OriginGuard puts on everything it serves.
// They have to be here too: in development the pages come from Vite, not
// from the API port, and OriginGuard trusts an Origin of localhost:5173. A
// hostile page that iframes this dev server and harvests a click gets a
// proxied POST carrying that trusted Origin -- the exact exploit the guard
// exists to stop, routed around it. Proxied /api responses already carry
// these from JARVIS; the HTML itself did not.
const SECURITY_HEADERS = {
  "X-Frame-Options": "DENY",
  "Content-Security-Policy": "frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

export default defineConfig({
  server: {
    port: 5173,
    headers: SECURITY_HEADERS,
    // The page imports the transcription protocol from the Python package,
    // which both sides read.
    fs: {
      allow: [searchForWorkspaceRoot(process.cwd()), resolve(__dirname, "../jarvis/transcribeProtocol.json")],
    },
    proxy: {
      "/ws": {
        target: "https://localhost:8340",
        ws: true,
        secure: false,
      },
      "/api": {
        target: "https://localhost:8340",
        secure: false,
      },
    },
  },
  preview: {
    headers: SECURITY_HEADERS,
  },
  build: {
    // Inside the Python package, so the wheel ships the built pages.
    outDir: "../jarvis/web",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        dashboard: resolve(__dirname, "dashboard.html"),
      },
    },
  },
});
