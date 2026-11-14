import { defineConfig, searchForWorkspaceRoot } from "vite";
import { createHmac, timingSafeEqual } from "crypto";
import { existsSync, readFileSync, statSync } from "fs";
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

// The port and HTTPS chosen in Settings, Server: the start file JARVIS
// keeps (jarvis/start_settings.py), trusted only when its signature (keyed
// with the tool token) checks, exactly as JARVIS trusts it. JARVIS_DATA_DIR
// is the data folder of an isolated instance, set by its parent.
const DATA_DIR = process.env.JARVIS_DATA_DIR || resolve(__dirname, "../data");
const START_FILE = resolve(DATA_DIR, "settings/start.json");

function chosenTarget(): string {
  const data = DATA_DIR;
  const certs = existsSync(resolve(__dirname, "../cert.pem")) && existsSync(resolve(__dirname, "../key.pem"));
  let port = 8340;
  let https = certs;
  try {
    const file = JSON.parse(readFileSync(START_FILE, "utf8"));
    const token = readFileSync(resolve(data, "jarvis/tool-token"), "utf8");
    const body = JSON.stringify(sortKeys(file.body));
    const sig = createHmac("sha256", token).update(body).digest("hex");
    if (timingSafeEqual(Buffer.from(sig), Buffer.from(String(file.sig)))) {
      port = Number(file.body.values?.port ?? port);
      https = certs && file.body.values?.https !== false;
    }
  } catch { /* no start file, or one not to trust: the defaults */ }
  return `${https ? "https" : "http"}://localhost:${port}`;
}

// Python's json.dumps(sort_keys=True, separators=(",", ":")): the body the
// start file's signature covers.
function sortKeys(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.keys(v as object).sort()
      .map((k) => [k, sortKeys((v as Record<string, unknown>)[k])]));
  }
  return v;
}

// Where the dev server sends /api and /ws: the JARVIS Settings chose, worked
// out again whenever the start file changes, so a port or HTTPS changed in
// Settings, Server reaches the running dev server once JARVIS restarts on
// it, with no restart of Vite. An isolated instance on another port is
// named by its parent with JARVIS_API_TARGET (and its own JARVIS_VITE_PORT),
// so a test page never talks to the live backend.
const FIXED_TARGET = process.env.JARVIS_API_TARGET;
const VITE_PORT = Number(process.env.JARVIS_VITE_PORT || 5173);
let seen = { stamp: "", target: "" };

function currentTarget(): string {
  if (FIXED_TARGET) return FIXED_TARGET;
  let stamp = "none";
  try {
    const st = statSync(START_FILE);
    stamp = `${st.mtimeMs}:${st.size}`;
  } catch { /* no start file: the defaults */ }
  if (stamp !== seen.stamp || !seen.target) seen = { stamp, target: chosenTarget() };
  return seen.target;
}

// http-proxy reads `options.target` on each request (a WebSocket upgrade
// uses the options object itself), so setting it just before each one
// sends every request to the target in force now.
function followTarget(proxy: any, options: { target?: unknown }): void {
  for (const kind of ["web", "ws"] as const) {
    const pass = proxy[kind].bind(proxy);
    proxy[kind] = (...args: unknown[]) => {
      options.target = currentTarget();
      return pass(...args);
    };
  }
}

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
        target: currentTarget(),
        ws: true,
        secure: false,
        configure: followTarget,
      },
      "/api": {
        target: currentTarget(),
        secure: false,
        configure: followTarget,
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
