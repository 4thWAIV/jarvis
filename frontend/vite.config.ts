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

// Where the dev server sends /api and /ws: the live JARVIS by default. An
// isolated instance on another port sets JARVIS_API_TARGET (and its own
// JARVIS_VITE_PORT), so a test page never talks to the live backend.
const API_TARGET = process.env.JARVIS_API_TARGET || "https://localhost:8340";
const VITE_PORT = Number(process.env.JARVIS_VITE_PORT || 5173);

export default defineConfig({
  server: {
    port: VITE_PORT,
    // The port is that port or nothing: Chrome keeps the microphone
    // permission per origin, port included, so a page that quietly moved to
    // 5174 would lose it. A port in use is reported, never worked around.
    strictPort: true,
    headers: SECURITY_HEADERS,
    // The page imports the transcription protocol from the Python package,
    // which both sides read.
    fs: {
      allow: [searchForWorkspaceRoot(process.cwd()), resolve(__dirname, "../jarvis/transcribeProtocol.json")],
    },
    proxy: {
      "/ws": {
        target: API_TARGET,
        ws: true,
        secure: false,
      },
      "/api": {
        target: API_TARGET,
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
