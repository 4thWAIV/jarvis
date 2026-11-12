"""Run JARVIS from a source checkout in the background: the backend and the
Vite dev server, each a process of its own that outlives the terminal that
started it, each with its own log.

    python -m jarvis.background certs     the development certificate, once
    python -m jarvis.background start     both, refused while either runs
    python -m jarvis.background status    what runs, where, and its log
    python -m jarvis.background stop      both, and only what this started
    python -m jarvis.background restart   stop, then start
    python -m jarvis.background logs [backend|vite] [-n LINES]

Nothing here is a supervisor: a process that exits stays exited, and
`status` says so. What is ours is decided by identity, never by a pid alone
(`process_inspect`):

- the backend is the process the server itself recorded at startup
  (`server_address`, its pid and, where the OS gives one, its creation
  token), so a backend that restarted itself (`/api/restart`, which starts
  a new process on Windows) is still found, and a reused pid is not;
- Vite is the process this command started, recorded with its creation
  token in `data_paths.background_path()`.

`stop` ends only a process whose identity still matches, with its own
children (the knowledge engine, a brain, esbuild): `taskkill /T` on
Windows, the process group on macOS and Linux. Anything else, an unrelated
process on a reused pid included, is left alone and reported.

The ports are the ones the page is served from: the backend's 8340 and
Vite's 5173 (vite.config.ts, strictPort), since Chrome keeps the microphone
permission per origin, port included. An occupied port is reported by the
process that could not bind it, in its log.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from jarvis import data_paths, process_inspect, server_address

WINDOWS = sys.platform == "win32"
BACKEND_PORT = 8340
VITE_PORT = 5173
START_WAIT_SEC = 90.0
STOP_WAIT_SEC = 15.0
NAMES = ("backend", "vite")


# -- where things are ---------------------------------------------------------

def checkout() -> Path:
    if not data_paths.CHECKOUT:
        raise SystemExit("jarvis.background runs a source checkout; this is an installed copy.")
    return data_paths.CONFIG_DIR


def logs_dir() -> Path:
    path = data_paths.data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(name: str) -> Path:
    return logs_dir() / f"{name}.log"


def state_path() -> Path:
    return data_paths.data_dir() / "background.json"


def _read_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data_paths._write_atomically(path, json.dumps(state, indent=2) + "\n")


# -- the processes ------------------------------------------------------------

def _identity(pid: int) -> Optional[process_inspect.Identity]:
    return process_inspect.inspect_process(pid).identity


class Found:
    """One of the two, as far as can be confirmed now."""

    def __init__(self, name: str, pid: Optional[int], state: process_inspect.State,
                 detail: str = ""):
        self.name, self.pid, self.state, self.detail = name, pid, state, detail

    @property
    def running(self) -> bool:
        return self.state is process_inspect.State.ALIVE

    def line(self) -> str:
        where = {"backend": f"port {BACKEND_PORT}", "vite": f"http://localhost:{VITE_PORT}"}[self.name]
        if self.pid is None:
            return f"{self.name}: not running ({where}); log {log_path(self.name)}"
        word = {"alive": "running", "exited": "not running", "unknown": "unconfirmed"}[self.state.value]
        extra = f", {self.detail}" if self.detail else ""
        return f"{self.name}: {word}, pid {self.pid}{extra} ({where}); log {log_path(self.name)}"


def find_backend() -> Found:
    """The backend the server recorded for itself (server_address)."""
    found = server_address.running()
    if found is None:
        return Found("backend", None, process_inspect.State.EXITED)
    inspected = process_inspect.inspect_process(found.pid, found.identity)
    detail = found.url
    if found.identity is None and WINDOWS:
        detail += ", recorded without its identity"
    return Found("backend", found.pid, inspected.state, detail)


def find_vite() -> Found:
    recorded = _read_state().get("vite") or {}
    pid = recorded.get("pid")
    if not pid:
        return Found("vite", None, process_inspect.State.EXITED)
    token = recorded.get("start_token")
    identity = process_inspect.Identity(int(pid), token) if token else None
    inspected = process_inspect.inspect_process(int(pid), identity)
    detail = "" if inspected.state is process_inspect.State.ALIVE else inspected.reason
    return Found("vite", int(pid), inspected.state, detail)


def _detached() -> dict:
    """Popen options for a process that outlives this terminal."""
    if WINDOWS:
        # A console of its own, never shown: closing the terminal that ran
        # this sends that terminal's console its close event, not this one,
        # and the children it starts (the engine, a brain) share the hidden
        # console instead of each opening a window.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def _spawn(name: str, argv: list[str], cwd: Path) -> subprocess.Popen:
    log = open(log_path(name), "ab")
    try:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start: {' '.join(argv)}\n".encode())
        log.flush()
        return subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, **_detached())
    finally:
        log.close()


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node was not found on PATH; install Node 22 (see the README).")
    return node


def _vite_argv(root: Path) -> list[str]:
    """Vite's own script under node, not npm's launcher: on Windows npm is a
    batch file, and the process to own is Vite itself."""
    script = root / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    if not script.is_file():
        raise SystemExit(f"{script} is missing; run npm install (or npm ci) in frontend first.")
    return [_node(), str(script)]


def _port_open(port: int) -> bool:
    for host in ("127.0.0.1", "::1"):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def start(wait: bool = True) -> int:
    root = checkout()
    backend, vite = find_backend(), find_vite()
    already = [f.line() for f in (backend, vite) if f.running]
    if already:
        print("Already running; nothing started (stop it first):")
        print("\n".join(already))
        return 1
    busy = [p for p in (BACKEND_PORT, VITE_PORT) if _port_open(p)]
    if busy:
        print(f"Port(s) {', '.join(map(str, busy))} are in use by another program; nothing "
              f"started. JARVIS's page must be served from these ports.")
        return 1
    if not ((root / "cert.pem").is_file() and (root / "key.pem").is_file()):
        print("cert.pem and key.pem are missing from the checkout; run "
              "`python -m jarvis.background certs` first (the Vite proxy speaks HTTPS).")
        return 1
    _spawn("backend", [sys.executable, "-m", "jarvis", "--host", "127.0.0.1",
                       "--port", str(BACKEND_PORT)], root)
    proc = _spawn("vite", _vite_argv(root), root / "frontend")
    identity = _identity(proc.pid)
    _write_state({"vite": {"pid": proc.pid,
                           "start_token": identity.start_token if identity else None,
                           "started_at": time.time()}})
    if not wait:
        return 0
    deadline = time.monotonic() + START_WAIT_SEC
    while time.monotonic() < deadline:
        backend, vite = find_backend(), find_vite()
        if backend.running and vite.running and _port_open(VITE_PORT):
            print(backend.line())
            print(vite.line())
            print(f"Open http://localhost:{VITE_PORT} in Chrome.")
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    print("Did not come up; see the logs:")
    print(find_backend().line())
    print(find_vite().line())
    return 1


def _end_tree(pid: int) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_one(found: Found) -> bool:
    """End `found` if it is still the process recorded; True once it is not
    running."""
    if found.pid is None or found.state is process_inspect.State.EXITED:
        return True
    if not found.running:
        print(f"{found.name}: pid {found.pid} could not be confirmed as ours; left alone.")
        return False
    _end_tree(found.pid)
    deadline = time.monotonic() + STOP_WAIT_SEC
    while time.monotonic() < deadline:
        again = find_backend() if found.name == "backend" else find_vite()
        if not again.running or again.pid != found.pid:
            return True
        time.sleep(0.25)
    if not WINDOWS:
        try:
            os.killpg(os.getpgid(found.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return True
    print(f"{found.name}: pid {found.pid} did not stop.")
    return False


def stop() -> int:
    checkout()
    ok = True
    for found in (find_vite(), find_backend()):
        stopped = _stop_one(found)
        ok = ok and stopped
        if stopped and found.pid is not None:
            print(f"{found.name}: stopped (pid {found.pid}).")
    if ok:
        state = _read_state()
        state.pop("vite", None)
        _write_state(state)
    return 0 if ok else 1


def status() -> int:
    checkout()
    found = (find_backend(), find_vite())
    for f in found:
        print(f.line())
    return 0 if all(f.running for f in found) else 3


def logs(name: str, lines: int) -> int:
    path = log_path(name)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"{path} does not exist yet.")
        return 1
    sys.stdout.write("\n".join(text.splitlines()[-lines:]) + "\n")
    return 0


# -- the development certificate ---------------------------------------------

def _openssl() -> Optional[str]:
    found = shutil.which("openssl")
    if found:
        return found
    if WINDOWS:
        git = shutil.which("git")
        candidates = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "openssl.exe"]
        if git:
            candidates.insert(0, Path(git).resolve().parent.parent / "usr" / "bin" / "openssl.exe")
        for c in candidates:
            if c.is_file():
                return str(c)
    return None


def certs() -> int:
    """cert.pem and key.pem for localhost in the checkout, made once by the
    openssl Git for Windows (or the system) ships."""
    root = checkout()
    cert, key = root / "cert.pem", root / "key.pem"
    if cert.is_file() and key.is_file():
        print(f"{cert} and {key} are already there.")
        return 0
    openssl = _openssl()
    if openssl is None:
        print("openssl was not found. It comes with Git for Windows (usr\\bin\\openssl.exe); "
              "install Git for Windows, or put openssl on PATH.")
        return 1
    result = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
                             "-out", str(cert), "-days", "365", "-nodes", "-subj", "/CN=localhost"],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    if result.returncode != 0:
        print(result.stdout + result.stderr)
        return 1
    print(f"Made {cert} and {key} with {openssl}.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.background",
                                     description="Run JARVIS's backend and Vite in the background.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("certs")
    start_p = sub.add_parser("start")
    start_p.add_argument("--no-wait", action="store_true")
    sub.add_parser("status")
    sub.add_parser("stop")
    sub.add_parser("restart")
    logs_p = sub.add_parser("logs")
    logs_p.add_argument("name", nargs="?", choices=NAMES, default="backend")
    logs_p.add_argument("-n", "--lines", type=int, default=60)
    args = parser.parse_args(argv)
    # A Windows console's code page cannot show every character Vite logs
    # (its arrow, measured); a character it cannot show is replaced, never
    # an error that hides the rest of the log.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.command == "certs":
        return certs()
    if args.command == "start":
        return start(wait=not args.no_wait)
    if args.command == "status":
        return status()
    if args.command == "stop":
        return stop()
    if args.command == "restart":
        code = stop()
        return code if code else start()
    return logs(args.name, args.lines)


if __name__ == "__main__":
    sys.exit(main())
