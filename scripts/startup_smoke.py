"""The README's source startup, checked from outside: what the Windows
startup workflow runs after `python -m jarvis.background start` in an
earlier step, so the processes have already outlived the shell that started
them.

    python scripts/startup_smoke.py running     page, proxy, socket, brain
    python scripts/startup_smoke.py lifecycle   duplicate start, restart, stop

Every check prints one line, PASS or FAIL, with what it saw; the exit
status is the number of failures. Standard library plus `websockets`, which
JARVIS already depends on.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

PAGE = "http://localhost:5173"
API = "https://127.0.0.1:8340"
ORIGIN = PAGE
FAILURES: list[str] = []


def check(name: str, ok: bool, saw: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'} {name}: {saw}", flush=True)
    if not ok:
        FAILURES.append(name)
    return ok


def get(url: str, *, origin: bool = False, timeout: float = 10.0, method: str = "GET"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # the development certificate is self-signed
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    if origin:
        req.add_header("Origin", ORIGIN)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return None, f"{type(e).__name__}: {e}"


def background(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "jarvis.background", *args],
                          capture_output=True, text=True, timeout=180)


def wait_for(fn, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(1)
    return False


async def _socket_snapshot() -> str:
    import websockets
    async with websockets.connect("ws://localhost:5173/ws/sessions",
                                  additional_headers={"Origin": ORIGIN},
                                  open_timeout=15) as ws:
        frame = json.loads(await asyncio.wait_for(ws.recv(), 15))
        return frame.get("type", "")


def running() -> None:
    out = background("status")
    check("status says both run", out.returncode == 0, out.stdout.strip().replace("\n", " | "))
    code, body = get(f"{API}/api/health")
    check("backend answers over HTTPS", code == 200 and "online" in body, f"{code} {body[:80]}")
    code, body = get(f"{PAGE}/")
    check("page loads from Vite", code == 200 and "<html" in body.lower(), f"{code}, {len(body)} bytes")
    code, body = get(f"{PAGE}/api/health")
    check("API proxied through Vite", code == 200 and "online" in body, f"{code} {body[:80]}")
    try:
        kind = asyncio.run(_socket_snapshot())
        check("WebSocket proxied through Vite", kind == "snapshot", f"first frame {kind!r}")
    except Exception as e:
        check("WebSocket proxied through Vite", False, f"{type(e).__name__}: {e}")
    code, body = get(f"{PAGE}/api/brain")
    check("brain path reported", code == 200, f"{code} {body[:120]}")
    code, body = get(f"{PAGE}/api/diagnostics/subsystems")
    print(f"INFO subsystems: {code} {body[:1500]}")


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def lifecycle() -> None:
    out = background("start", "--no-wait")
    check("a second start is refused", out.returncode != 0 and "Already running" in out.stdout,
          out.stdout.strip().replace("\n", " | "))
    before = background("status").stdout
    code, body = get(f"{PAGE}/api/restart", origin=True, method="POST")
    check("backend restart asked", code == 200, f"{code} {body[:80]}")
    time.sleep(5)
    came_back = wait_for(lambda: get(f"{API}/api/health")[0] == 200, 90)
    after = background("status")
    check("backend is back after its own restart", came_back and after.returncode == 0,
          f"before: {before.strip()} | after: {after.stdout.strip()}".replace("\n", " | "))
    # An unrelated process the stop must leave alive.
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    out = background("stop")
    check("stop ends both", out.returncode == 0, out.stdout.strip().replace("\n", " | "))
    check("an unrelated process survives stop", bystander.poll() is None, f"pid {bystander.pid}")
    bystander.kill()
    gone = wait_for(lambda: _port_free(8340) and _port_free(5173), 30)
    check("both ports are free after stop", gone, "8340 and 5173")
    out = background("status")
    check("status says neither runs", out.returncode == 3, out.stdout.strip().replace("\n", " | "))
    # A port someone else holds: start refuses and says which.
    holder = socket.socket()
    holder.bind(("127.0.0.1", 5173))
    holder.listen()
    try:
        out = background("start", "--no-wait")
        check("an occupied port is reported", out.returncode != 0 and "5173" in out.stdout,
              out.stdout.strip())
    finally:
        holder.close()
    out = background("start")
    check("start again after stop", out.returncode == 0, out.stdout.strip().replace("\n", " | "))
    out = background("stop")
    check("stop again", out.returncode == 0, out.stdout.strip().replace("\n", " | "))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "running"
    {"running": running, "lifecycle": lifecycle}[what]()
    print(f"{len(FAILURES)} failed: {FAILURES}" if FAILURES else "all passed")
    sys.exit(len(FAILURES))
