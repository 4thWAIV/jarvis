"""Checking a built decision package end to end in a real, isolated JARVIS,
before it goes to Tim (decision_package.py).

The isolated JARVIS is JARVIS's own server (decision_check_server.py) in a
child process on a free loopback port above 19000, with its own temporary
data folder, a temporary copy of the .env, no speech model and the brain
never spawned. The voice page is built with vite into a cached temporary
folder (never jarvis/web), which that JARVIS serves, so the page, its
sockets and the package frame have its own origin, as the live page has.

The package is unpacked there through the same route delivery uses, found
by its title's words with `list_packages` and opened by name with
`open_package_page`, both through that JARVIS's /internal/tool. Then, in the
real sandboxed package frame at each centre size (809 x 589 and 662 x 489):

- every slide fits: the deck inside the frame, nothing outside its slide,
  nothing scrolled or cut off, and every word from 13 to 25 px;
- every decision slide has its vote row in view;
- `tell_package_story` tells the story through JARVIS's real speech path
  (a silent stand-in voice), and it brings every decision slide up and
  points at every beat's targets;
- one Choose click saves its answer to results/decisions.json;
- one answer by voice (`run_package_command` running scripts/decide.py)
  appears on the slide.

Every slide is photographed at each size. Only the processes started here
are stopped, and nothing reaches the live JARVIS.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from jarvis import data_paths

FRONTEND = data_paths.SOURCE_ROOT / "frontend"
# The two centre frame sizes, in a 1440 x 900 and a 1280 x 800 window: the
# browser's inner size to start from, and the package frame (_fit_frame
# then makes it exactly that).
SIZES = (((1440, 813), (809, 589)), ((1280, 713), (662, 489)))
TEXT_MIN_PX, TEXT_MAX_PX = 13.0, 25.0
PORTS = range(19000, 29000)
STAND_IN_KEY = "stand-in-for-the-check"
READY_SEC = 90
BUILD_SEC = 300
STORY_SEC = 240
FOLLOW_SEC = 10          # the deck reads its record every 3 s
# Audio plays without a click, for the story; WebGL is off, so the orb
# stands still (as it does on a Mac without WebGL) instead of costing several
# cores in software rendering. The package frame does not use it.
BROWSER_ARGS = ["--autoplay-policy=no-user-gesture-required", "--disable-webgl",
                "--disable-3d-apis"]


@dataclass
class Result:
    passed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    screenshots: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and bool(self.passed)

    def expect(self, ok: bool, passed: str, failed: str) -> bool:
        (self.passed if ok else self.failures).append(passed if ok else failed)
        return ok


class CheckFailed(Exception):
    """A step the rest of the check cannot go on without."""


# -- the page ---------------------------------------------------------------------------

def _newest_source() -> float:
    paths = list((FRONTEND / "src").rglob("*")) + [
        FRONTEND / name for name in ("index.html", "dashboard.html", "vite.config.ts",
                                     "package.json", "package-lock.json")]
    paths.append(data_paths.PACKAGE_DIR / "transcribeProtocol.json")
    return max((p.stat().st_mtime for p in paths if p.is_file()), default=0.0)


def built_page() -> Path:
    """The voice page built by vite into a temporary folder, kept while no
    source is newer, so a second package does not build it again."""
    key = hashlib.sha256(f"{FRONTEND}:{_newest_source()}".encode()).hexdigest()[:16]
    dist = Path(tempfile.gettempdir()) / f"jarvis-decision-page-{key}"
    if (dist / "index.html").is_file():
        return dist
    if not (FRONTEND / "node_modules").is_dir():
        raise CheckFailed(f"The page cannot be built: {FRONTEND / 'node_modules'} is missing "
                          f"(cd frontend && npm install).")
    partial = Path(tempfile.mkdtemp(prefix="jarvis-decision-page-"))
    built = subprocess.run(["npx", "vite", "build", "--outDir", str(partial), "--emptyOutDir"],
                           cwd=FRONTEND, capture_output=True, text=True, timeout=BUILD_SEC,
                           stdin=subprocess.DEVNULL)
    if built.returncode != 0 or not (partial / "index.html").is_file():
        shutil.rmtree(partial, ignore_errors=True)
        raise CheckFailed(f"vite could not build the page: {built.stderr[-600:]}")
    try:
        partial.rename(dist)
    except OSError:                       # another check built it meanwhile
        shutil.rmtree(partial, ignore_errors=True)
    return dist


# -- the isolated JARVIS ----------------------------------------------------------------

def free_port() -> int:
    for port in random.sample(PORTS, 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise CheckFailed("No free port above 19000.")


@contextlib.contextmanager
def _data_dir(path: Path):
    """data_paths answering for another data folder, for this block only."""
    before = os.environ.get("JARVIS_DATA_DIR")
    os.environ["JARVIS_DATA_DIR"] = str(path)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = before


class Isolated:
    """One JARVIS of our own: its folder, port, token and process."""

    def __init__(self, folder: Path, language: str, page: Path):
        self.folder = folder
        self.data = folder / "data"
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = folder / "jarvis.log"
        env_copy = folder / "env"
        live_env = data_paths.env_file_path()
        lines = live_env.read_text().splitlines() if live_env.is_file() else []
        # The Fish key is never called (the voice is a stand-in), and never
        # copied; one is set, so the page does not open first-time setup.
        lines = [line for line in lines if not line.strip().startswith("FISH_API_KEY")]
        env_copy.write_text("\n".join(lines + [f"FISH_API_KEY={STAND_IN_KEY}"]) + "\n")
        env_copy.chmod(0o600)
        env = {**os.environ,
               "JARVIS_DATA_DIR": str(self.data), "JARVIS_ENV_FILE": str(env_copy),
               "JARVIS_STT_MODEL_DIR": str(folder / "models"),
               "JARVIS_BRAIN_AUTOSTART": "0", "JARVIS_FOLDER_DISCOVERY": "0",
               "JARVIS_REFUSE_UNTRUSTED_ACTIONS": "0", "JARVIS_TTS_PROVIDER": "fish",
               "FISH_API_KEY": STAND_IN_KEY,
               "PYTHONUNBUFFERED": "1"}
        with self.log.open("wb") as log:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "jarvis.decision_check_server", "--port", str(self.port),
                 "--language", language, "--page", str(page)],
                cwd=str(data_paths.SOURCE_ROOT), env=env, stdout=log, stderr=log,
                stdin=subprocess.DEVNULL, start_new_session=True)
        self._wait_ready()
        with _data_dir(self.data):
            self.token = data_paths.ensure_tool_token()

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_SEC
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise CheckFailed(f"The isolated JARVIS stopped at startup; see {self.log}.")
            try:
                with urllib.request.urlopen(self.base + "/api/packages", timeout=2):
                    if (self.data / "jarvis" / "tool-token").is_file():
                        return
            except OSError:
                pass
            time.sleep(0.5)
        raise CheckFailed(f"The isolated JARVIS did not start in {READY_SEC}s; see {self.log}.")

    def tool(self, tool: str, /, **arguments) -> tuple[bool, str]:
        """One brain tool through this JARVIS's /internal/tool: (ok, its text)."""
        from jarvis.decision_package import post
        answer = post(self.base + "/internal/tool", self.token,
                      {"tool": tool, "arguments": arguments}, timeout=120)
        return bool(answer.get("ok")), str(answer.get("text") or "")

    def package_folder(self, package_id: str) -> Path:
        return self.data / "packages" / package_id

    def stop(self) -> None:
        """Stop the process group started here, and only it."""
        if self.process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(15)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(5)


# -- in the frame -----------------------------------------------------------------------

# Recorded in the package's frame: each slide as it becomes the current one,
# and each beat the story helper rings.
RECORD = """
(() => {
  if (window === window.top) return;
  window.__shown = []; window.__rings = [];
  new MutationObserver((records) => {
    for (const r of records) {
      const t = r.target;
      if (r.type === "attributes" && t.classList && t.classList.contains("slide")
          && t.classList.contains("current") && window.__shown[window.__shown.length - 1] !== t.id)
        window.__shown.push(t.id);
      for (const n of r.addedNodes || [])
        if (n.classList && n.classList.contains("jarvis-story-ring")) window.__rings.push(n.dataset.beat);
    }
  }).observe(document, {subtree: true, childList: true, attributes: true, attributeFilter: ["class"]});
})();
"""

# What is wrong with the slide showing now: [] when it fits.
MEASURE = """
() => {
  const W = 880, problems = [];
  const stage = document.getElementById("stage");
  const slide = document.querySelector("#track > .slide.current");
  const s = stage.getBoundingClientRect(), box = slide.getBoundingClientRect();
  const scale = s.width / W;
  const say = (el) => (el.textContent || el.placeholder || el.tagName).trim().replace(/\\s+/g, " ").slice(0, 40);
  if (s.left < -1 || s.top < -1 || s.right > innerWidth + 1 || s.bottom > innerHeight + 1)
    problems.push(`the deck (${Math.round(s.width)} x ${Math.round(s.height)}) does not fit the frame (${innerWidth} x ${innerHeight})`);
  if (slide.scrollHeight > slide.clientHeight + 1 || slide.scrollWidth > slide.clientWidth + 1)
    problems.push(`its content is ${slide.scrollHeight - slide.clientHeight}px taller than the slide`);
  const sizes = [];
  for (const el of stage.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    if (cs.visibility !== "visible" || el.closest("[hidden], .hidden-title")) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const inSlide = slide.contains(el);
    if (inSlide && (r.left < box.left - 1 || r.top < box.top - 1 || r.right > box.right + 1 || r.bottom > box.bottom + 1))
      problems.push(`"${say(el)}" runs outside the slide`);
    if (inSlide && (cs.overflowX !== "visible" || cs.overflowY !== "visible")
        && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1))
      problems.push(`"${say(el)}" is cut off or scrolls`);
    const words = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())
      || (el.tagName === "INPUT" && (el.value || el.placeholder));
    if (!words) continue;
    const px = parseFloat(cs.fontSize) * scale;
    sizes.push(px);
    if (px < MIN - 0.05) problems.push(`"${say(el)}" is ${px.toFixed(1)}px, under ${MIN}px`);
    if (px > MAX + 0.05) problems.push(`"${say(el)}" is ${px.toFixed(1)}px, over ${MAX}px`);
  }
  if (slide.classList.contains("decision")) {
    for (const el of slide.querySelectorAll(".vote, .choose")) {
      const r = el.getBoundingClientRect();
      if (r.top < box.top - 1 || r.bottom > box.bottom + 1 || r.bottom > innerHeight + 1 || r.right > innerWidth + 1)
        problems.push(`its ${el.className} is not in view`);
    }
  }
  return {problems, smallest: Math.min(...sizes), largest: Math.max(...sizes)};
}
""".replace("MIN", str(TEXT_MIN_PX)).replace("MAX", str(TEXT_MAX_PX))


async def _check_size(browser, jarvis: Isolated, deck: dict, story: dict,
                      package_id: str, window: tuple[int, int], frame_size: tuple[int, int],
                      shots: Path, result: Result) -> None:
    label = f"{frame_size[0]}x{frame_size[1]}"
    folder = jarvis.package_folder(package_id)
    shutil.rmtree(folder / "results", ignore_errors=True)
    context = await browser.new_context(viewport={"width": window[0], "height": window[1]})
    page = await context.new_page()
    stories: list[dict] = []

    def heard(ws):
        if ws.url.endswith("/ws/voice"):
            ws.on("framereceived", lambda payload: _story_frame(payload, stories))

    page.on("websocket", heard)
    await page.add_init_script(RECORD)
    try:
        await page.goto(jarvis.base + "/", wait_until="load")
        # Open by name, once the page is on JARVIS's voice socket.
        deadline, said, ok = time.monotonic() + 30, "", False
        while time.monotonic() < deadline:
            ok, said = await asyncio.to_thread(jarvis.tool, "open_package_page",
                                               package=deck["title"])
            if ok and "No JARVIS page is open" not in said:
                break
            await asyncio.sleep(0.5)
        if not result.expect(ok and package_id in said,
                             f"{label}: open_package_page opened it by its title",
                             f"{label}: open_package_page by its title said: {said}"):
            return
        iframe = page.locator("#package-view iframe")
        await iframe.wait_for(timeout=20000)
        inner = None
        for _ in range(100):
            inner = next((f for f in page.frames if f"/{package_id}/files/" in f.url), None)
            if inner is not None and await inner.locator("#go-1").count():
                break
            await asyncio.sleep(0.2)
        if inner is None:
            raise CheckFailed(f"{label}: the package's frame never loaded")
        size = await _fit_frame(page, iframe, frame_size)
        if not result.expect(size == frame_size, f"{label}: the package frame is {label}",
                             f"{label}: the package frame stayed {size[0]}x{size[1]}"):
            return

        slides = await inner.eval_on_selector_all(
            "#track > .slide", "els => els.map(e => [e.id, e.classList.contains('decision')])")
        bad = 0
        for n, (slide_id, _decision) in enumerate(slides, 1):
            await _fit_frame(page, iframe, frame_size)
            await inner.click(f"#go-{n}")
            await inner.wait_for_selector(f"#{slide_id}.current")
            measured = await inner.evaluate(MEASURE)
            shot = shots / label / f"{n:02d}-{slide_id}.png"
            await iframe.screenshot(path=str(shot))
            result.screenshots.append(shot)
            for problem in measured["problems"]:
                bad += 1
                result.failures.append(f"{label} slide {n} ({slide_id}): {problem} [{shot}]")
        if not bad:
            result.passed.append(f"{label}: all {len(slides)} slides fit, text 13 to 25 px"
                                 + (", every vote row in view" if deck.get("decisions") else ""))

        await _fit_frame(page, iframe, frame_size)
        await _check_story(jarvis, inner, story, package_id, slides, stories, label, result)
        if deck.get("decisions"):          # a presentation has nothing to answer
            await _check_choose(inner, deck, folder, label, shots, iframe, result)
            await _check_voice(jarvis, inner, deck, package_id, label, shots, iframe, result)
    finally:
        if result.failures:
            shot = shots / label / "last.png"
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(shot))
                result.screenshots.append(shot)
        await context.close()


async def _fit_frame(page, iframe, frame_size: tuple[int, int]) -> tuple[int, int]:
    """Size the window so the package frame is exactly `frame_size`: the
    lines the centre shows above its views (the sign-in reminder, updates)
    come and go, and the package is checked at the frame size, not at the
    window's. The frame's size after."""
    size = (0, 0)
    for _ in range(4):
        box = await iframe.bounding_box()
        size = (round(box["width"]), round(box["height"]))
        if size == frame_size:
            break
        view = page.viewport_size
        await page.set_viewport_size({"width": view["width"] + frame_size[0] - size[0],
                                      "height": view["height"] + frame_size[1] - size[1]})
        await asyncio.sleep(0.3)
    return size


def _story_frame(payload, stories: list[dict]) -> None:
    try:
        frame = json.loads(payload)
    except (TypeError, ValueError):
        return
    if isinstance(frame, dict) and frame.get("type") == "package_story":
        stories.append(frame)


async def _check_story(jarvis, inner, story, package_id, slides, stories, label, result):
    await inner.evaluate("() => { window.__shown = []; window.__rings = []; }")
    ok, said = await asyncio.to_thread(jarvis.tool, "tell_package_story", package=package_id)
    if not result.expect(ok, f"{label}: tell_package_story started",
                         f"{label}: tell_package_story said: {said}"):
        return
    deadline = time.monotonic() + STORY_SEC
    while time.monotonic() < deadline and not any(
            f.get("package") == package_id and f.get("state") in ("finished", "stopped")
            for f in stories):
        await asyncio.sleep(0.5)
    ended = next((f["state"] for f in stories if f.get("state") in ("finished", "stopped")), None)
    result.expect(ended == "finished", f"{label}: the story was told to the end",
                  f"{label}: the story {'was ' + ended if ended else 'did not end'} within "
                  f"{STORY_SEC}s")
    shown = set(await inner.evaluate("() => window.__shown"))
    # A presentation's story must reach every slide; a decision deck's,
    # every decision slide.
    voting = any(decision for _, decision in slides)
    missed = [s for s, decision in slides if (decision or not voting) and s not in shown]
    result.expect(not missed, f"{label}: the story reached every "
                              f"{'decision slide' if voting else 'slide'}",
                  f"{label}: the story never brought up {', '.join(missed)}")
    rung = set(await inner.evaluate("() => window.__rings"))
    beats = [f"{n}.{b}" for n, s in enumerate(story["slides"], 1)
             for b, beat in enumerate(s["beats"], 1) if beat.get("targets")]
    unrung = [b for b in beats if b not in rung]
    result.expect(not unrung, f"{label}: every beat pointed at its targets",
                  f"{label}: story beats {', '.join(unrung)} pointed at nothing (a target "
                  f"is missing or the beat was not heard)")


def _slide_of(decision_id: str) -> str:
    return f"#d-{decision_id}"


async def _show(inner, decision_id: str) -> None:
    number = await inner.evaluate(
        "(id) => [...document.querySelectorAll('#track > .slide')].findIndex(s => s.id === id) + 1",
        f"d-{decision_id}")
    await inner.click(f"#go-{number}")


async def _check_choose(inner, deck, folder, label, shots, iframe, result):
    first = deck["decisions"][0]
    await _show(inner, first["id"])
    await inner.click(f"#opt-{first['id']}-{first['recommended']} .choose")
    try:
        await inner.wait_for_function(
            "() => document.getElementById('status').textContent === 'Saved.'", timeout=15000)
        saved = json.loads((folder / "results" / "decisions.json").read_text())
    except Exception as e:                         # a timeout, or no record written
        result.failures.append(f"{label}: Choose on {first['id']} did not save: {e}")
        return
    shot = shots / label / "choose.png"
    await iframe.screenshot(path=str(shot))
    result.screenshots.append(shot)
    result.expect(saved.get(first["id"], {}).get("answer") == first["recommended"],
                  f"{label}: Choose saved {first['id']} to results/decisions.json",
                  f"{label}: Choose on {first['id']} saved {saved}")


async def _check_voice(jarvis, inner, deck, package_id, label, shots, iframe, result):
    asked = {c for d in deck["decisions"] for o in d["options"] for c in o.get("opens") or []}
    tops = [d for d in deck["decisions"] if d["id"] not in asked]
    decision = tops[-1] if len(tops) > 1 else tops[0]
    option = next(o for o in decision["options"] if o["id"] != decision["recommended"])
    ok, said = await asyncio.to_thread(
        jarvis.tool, "run_package_command", package=package_id,
        command=f'"$JARVIS_PYTHON" scripts/decide.py {decision["id"]} {option["id"]}')
    if not result.expect(ok and "Recorded:" in said,
                         f"{label}: decide.py recorded {decision['id']} by voice",
                         f"{label}: decide.py said: {said}"):
        return
    await _show(inner, decision["id"])
    wanted = f"Recorded: {option['name']}"
    try:
        await inner.wait_for_function(
            "([sel, want]) => document.querySelector(sel).textContent.startsWith(want)",
            arg=[f"{_slide_of(decision['id'])} .recorded", wanted], timeout=FOLLOW_SEC * 1000)
        shown = True
    except Exception:
        shown = False
    shot = shots / label / "voice.png"
    await iframe.screenshot(path=str(shot))
    result.screenshots.append(shot)
    result.expect(shown, f"{label}: the spoken answer appeared on screen",
                  f"{label}: the spoken answer to {decision['id']} never showed "
                  f"\"{wanted}\" [{shot}]")


async def _in_browser(jarvis: Isolated, deck: dict, story: dict,
                      package_id: str, shots: Path, result: Result) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            for window, frame_size in SIZES:
                (shots / f"{frame_size[0]}x{frame_size[1]}").mkdir(parents=True, exist_ok=True)
                await _check_size(browser, jarvis, deck, story, package_id, window,
                                  frame_size, shots, result)
        finally:
            await browser.close()


# -- the whole check --------------------------------------------------------------------

def check(archive: Path, deck: dict, folder: Path) -> Result:
    """Check the zip in an isolated JARVIS; screenshots go under `folder`."""
    from jarvis.decision_package import deliver
    result = Result()
    folder.mkdir(parents=True, exist_ok=True)
    jarvis = None
    try:
        dist = built_page()
        jarvis = Isolated(Path(tempfile.mkdtemp(prefix="jarvis-decision-check-")),
                          deck.get("language", "en"), dist)
        delivered = deliver(archive, jarvis.base, jarvis.token)
        package_id = delivered.package["id"]
        result.passed.append(f"unpacked in the isolated JARVIS as {package_id}")
        ok, said = jarvis.tool("list_packages", name=deck["title"])
        result.expect(ok and package_id in said,
                      f"list_packages found it by its title's words",
                      f"list_packages by \"{deck['title']}\" said: {said}")
        story = json.loads((jarvis.package_folder(package_id) / "story.json").read_text())
        asyncio.run(_in_browser(jarvis, deck, story, package_id, folder, result))
    except (CheckFailed, OSError, subprocess.SubprocessError) as e:
        result.failures.append(str(e))
    except Exception as e:                          # a browser that would not start, a crash
        result.failures.append(f"The check could not finish: {type(e).__name__}: {e}")
    finally:
        if jarvis is not None:
            jarvis.stop()
            if result.ok:
                shutil.rmtree(jarvis.folder, ignore_errors=True)
            else:
                result.failures.append(f"The isolated JARVIS's folder and log are kept at "
                                       f"{jarvis.folder}")
    return result
