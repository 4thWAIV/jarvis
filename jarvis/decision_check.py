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

- the deck's own styles are in force: deck.css loaded, the 880 x 640
  canvas styled, the page not the browser's default and one slide shown;
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
from typing import Optional

from jarvis import data_paths, decision_stamp, package_story

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
# How long the story may take to reach its next step (a slide's wait, its
# end, being stopped) before that step fails, naming it. A slide of the
# stand-in voice takes a few seconds.
STORY_SEC = 60
# The whole check, by the wall clock: a Mac that slept part way through, or
# anything else that stalls it, fails it in this time, never hangs it.
CHECK_SEC = 10 * 60
# One script run in the page or its frame, or one browser action.
EVAL_SEC = 15
FOLLOW_SEC = 10          # the deck reads its record every 3 s
# Longer than any beat of the stand-in voice: a story that waits for an
# answer says and shows nothing more in this time.
WAIT_LONGER_SEC = 3.0
# Stop ends the story's words and its cue within this.
STOP_WITHIN_SEC = 1.0
# Every vote control at least this tall, and every option's picture drawn
# at least this large each way, in the frame's own pixels.
MIN_CONTROL_PX = 32
MIN_PICTURE_PX = 60
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
    if not data_paths.CHECKOUT:
        if (data_paths.FRONTEND_DIST / "index.html").is_file():
            return data_paths.FRONTEND_DIST
        raise CheckFailed("The installed JARVIS page is missing from this distribution.")
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


# What is wrong with the deck's own styles in the frame: [] when deck.css is
# in force (a file the frame was refused is named beside it, Deck.refused).
# Without it the deck is the browser's default black text at default sizes
# on a bare page, every slide stacked, which the text sizes alone do not
# catch: the canvas is still scaled by deck.js.
STYLED = """
() => {
  const problems = [];
  const stage = document.getElementById("stage");
  const s = stage ? getComputedStyle(stage) : null;
  if (!s || s.position !== "absolute" || s.width !== "880px" || s.height !== "640px")
    problems.push(`its canvas is not styled (#stage is ${s ? s.position + " " + s.width + " x " + s.height : "missing"}, not absolute 880px x 640px)`);
  const body = getComputedStyle(document.body);
  if (body.backgroundColor === "rgba(0, 0, 0, 0)" || body.color === "rgb(0, 0, 0)")
    problems.push(`its page is the browser's default (background ${body.backgroundColor}, text ${body.color})`);
  const shown = [...document.querySelectorAll("#track > .slide")].filter((e) =>
    getComputedStyle(e).display !== "none" && getComputedStyle(e).visibility === "visible").length;
  if (shown > 1) problems.push(`${shown} slides show at once, not one`);
  return problems;
}
"""


# What is wrong with the voting on the decision slide showing now: every
# vote control visible, at least MIN_CONTROL px tall and inside the slide and
# the frame; exactly one Choose on each option; each option's picture drawn
# at least MIN_PICTURE px each way.
VOTING = """
() => {
  const problems = [];
  const slide = document.querySelector("#track > .slide.current");
  if (!slide.classList.contains("decision")) return problems;
  const box = slide.getBoundingClientRect();
  const say = (el) => (el.getAttribute("aria-label") || el.textContent || el.placeholder || el.tagName)
    .trim().replace(/\\s+/g, " ").slice(0, 40);
  for (const el of slide.querySelectorAll(".choose, .vote button, .vote .words")) {
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    if (cs.display === "none" || cs.visibility !== "visible" || parseFloat(cs.opacity) < 0.9 || !r.width)
      problems.push(`"${say(el)}" is not visible`);
    else if (r.height < MIN_CONTROL - 0.05)
      problems.push(`"${say(el)}" is ${r.height.toFixed(1)}px tall, under MIN_CONTROLpx`);
    if (r.top < box.top - 1 || r.bottom > box.bottom + 1 || r.left < -1 || r.right > innerWidth + 1
        || r.bottom > innerHeight + 1)
      problems.push(`"${say(el)}" is not inside the slide and the frame`);
  }
  for (const card of slide.querySelectorAll(".option")) {
    const n = card.querySelectorAll(".choose").length;
    if (n !== 1) problems.push(`option ${card.id} has ${n} Choose controls, not one`);
    for (const img of card.querySelectorAll(".picture img")) {
      const r = img.getBoundingClientRect();
      if (!img.naturalWidth) { problems.push(`option ${card.id}'s picture did not load`); continue; }
      const k = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
      const w = img.naturalWidth * k, h = img.naturalHeight * k;
      if (w < MIN_PICTURE || h < MIN_PICTURE)
        problems.push(`option ${card.id}'s picture is drawn ${Math.round(w)} x ${Math.round(h)}px, under MIN_PICTUREpx`);
    }
  }
  return problems;
}
""".replace("MIN_CONTROL", str(MIN_CONTROL_PX)).replace("MIN_PICTURE", str(MIN_PICTURE_PX))


class Deck:
    """The voice page in one browser context with the package open in its
    real frame, and every frame the voice socket brought, timed."""

    def __init__(self, browser, jarvis: Isolated, deck: dict, story: dict, package_id: str,
                 shots: Path, result: Result):
        self.browser, self.jarvis, self.deck, self.story = browser, jarvis, deck, story
        self.package_id, self.shots, self.result = package_id, shots, result
        self.parsed = package_story.parse(json.dumps(story).encode())
        self.frames: list[tuple[float, dict]] = []
        # Each file of the package the frame asked for and did not get (its
        # results are read before any exist, so they are not counted).
        self.refused: list[str] = []
        self.page = self.inner = self.iframe = None
        self.size = (0, 0)
        self.shot_n = 0

    def _not_got(self, url: str, why: str) -> None:
        mark = f"/{self.package_id}/files/"
        if mark in url:
            name = url.split(mark, 1)[1].split("?", 1)[0]
            if not name.startswith("results/"):
                self.refused.append(f"{name} {why}")

    def _answered(self, response):
        if response.status >= 400:
            self._not_got(response.url, f"answered {response.status}")

    def _failed(self, request):
        # The frame has no origin, so an error answer it was refused (a 404
        # read as a stylesheet) is blocked by the browser and arrives here.
        self._not_got(request.url, f"did not load ({request.failure})")

    def _heard(self, ws):
        if ws.url.endswith("/ws/voice"):
            ws.on("framereceived", self._frame)

    def _frame(self, payload):
        try:
            frame = json.loads(payload)
        except (TypeError, ValueError):
            return
        if isinstance(frame, dict):
            self.frames.append((time.monotonic(), frame))

    async def open(self, window: tuple[int, int], frame_size: tuple[int, int]) -> bool:
        """The page loaded, the package opened by its title, the frame sized."""
        label = f"{frame_size[0]}x{frame_size[1]}"
        self.size = frame_size
        context = await self.browser.new_context(viewport={"width": window[0],
                                                           "height": window[1]})
        # Every browser action and wait is bounded; none waits for ever.
        context.set_default_timeout(EVAL_SEC * 1000)
        context.set_default_navigation_timeout(60_000)
        self.page = await context.new_page()
        self.page.on("websocket", self._heard)
        self.page.on("response", self._answered)
        self.page.on("requestfailed", self._failed)
        await self.page.add_init_script(RECORD)
        await self.page.goto(self.jarvis.base + "/", wait_until="load")
        deadline, said, ok = time.monotonic() + 30, "", False
        while time.monotonic() < deadline:
            ok, said = await asyncio.to_thread(self.jarvis.tool, "open_package_page",
                                               package=self.deck["title"])
            if ok and "No JARVIS page is open" not in said:
                break
            await asyncio.sleep(0.5)
        if not self.result.expect(ok and self.package_id in said,
                                  f"{label}: open_package_page opened it by its title",
                                  f"{label}: open_package_page by its title said: {said}"):
            return False
        await self.find_frame()
        size = await self.fit()
        return self.result.expect(size == frame_size, f"{label}: the package frame is {label}",
                                  f"{label}: the package frame stayed {size[0]}x{size[1]}")

    async def find_frame(self) -> None:
        self.iframe = self.page.locator("#package-view iframe")
        await self.iframe.wait_for(timeout=20000)
        for _ in range(100):
            self.inner = next((f for f in self.page.frames
                               if f"/{self.package_id}/files/" in f.url), None)
            if self.inner is not None and await self.inner.locator("#go-1").count():
                return
            await asyncio.sleep(0.2)
        raise CheckFailed("the package's frame never loaded")

    async def reload(self) -> None:
        """A fresh page (the server shows the package again), so the deck
        reads the record as it is now."""
        await self.page.reload(wait_until="load")
        await self.find_frame()
        await self.fit()

    async def fit(self) -> tuple[int, int]:
        return await _fit_frame(self.page, self.iframe, self.size)

    async def close(self) -> None:
        if self.page is not None:
            await self.page.context.close()

    async def shoot(self, name: str, whole: bool = False) -> Path:
        self.shot_n += 1
        folder = self.shots / "flow"
        folder.mkdir(parents=True, exist_ok=True)
        shot = folder / f"{self.shot_n:02d}-{name}.png"
        with contextlib.suppress(Exception):
            if whole:
                await self.page.screenshot(path=str(shot))
            else:
                await self.iframe.screenshot(path=str(shot))
            self.result.screenshots.append(shot)
        return shot

    # -- the story's frames -----------------------------------------------------------

    def stories(self, since: float = 0.0) -> list[dict]:
        return [f for t, f in self.frames if t >= since and f.get("type") == "package_story"
                and f.get("package") == self.package_id]

    def cued_audio(self, since: float) -> list[dict]:
        return [f for t, f in self.frames if t >= since and f.get("type") == "audio"
                and isinstance(f.get("cue"), dict)
                and f["cue"].get("package") == self.package_id]

    async def story_frame(self, since: float, wanted, timeout: float) -> Optional[dict]:
        """The first story frame since `since` for which `wanted` holds, or
        None; also None as soon as a frame arrives that ends the story when
        that is not what was wanted."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for f in self.stories(since):
                if wanted(f):
                    return f
            await asyncio.sleep(0.05)
        return None

    async def tool(self, name: str, **arguments) -> tuple[bool, str]:
        return await asyncio.to_thread(self.jarvis.tool, name, **arguments)

    def clear_answers(self) -> None:
        shutil.rmtree(self.jarvis.package_folder(self.package_id) / "results",
                      ignore_errors=True)

    def answers(self) -> dict:
        path = self.jarvis.package_folder(self.package_id) / "results" / "decisions.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    async def current_slide(self) -> str:
        return await self.js(
            "() => (document.querySelector('#track > .slide.current') || {}).id || ''",
            what="reading the slide shown")

    async def rings(self) -> int:
        return await self.js("() => document.querySelectorAll('.jarvis-story-ring').length",
                             what="counting the story's rings")

    async def js(self, script: str, arg=None, what: str = "a script in the package frame"):
        """`script` run in the package's frame, given at most EVAL_SEC: a
        frame that does not answer fails the step it was for, by name."""
        run = self.inner.evaluate(script) if arg is None else self.inner.evaluate(script, arg)
        try:
            return await asyncio.wait_for(run, EVAL_SEC)
        except asyncio.TimeoutError:
            raise CheckFailed(f"{what}: the package frame did not answer in "
                              f"{EVAL_SEC}s") from None

    def heard_so_far(self, since: float) -> str:
        """What the voice socket brought since `since`, for a failure: how
        much of the story's audio was sent, and its last story frame."""
        audio = self.cued_audio(since)
        last = (self.stories(since) or [None])[-1]
        return (f"{len(audio)} chunk(s) of the story's audio were sent to the page; its "
                f"last story frame was {last}")

    async def press_stop(self) -> None:
        await self.page.locator("#package-view .package-story button").last.click()


async def _check_layout(d: Deck, label: str) -> None:
    """Every slide fits and, on a decision slide, every vote control is
    visible, tall enough and in the frame; each slide photographed."""
    result = d.result
    unstyled = await d.js(STYLED, what=f"{label}: checking the deck's styles")
    if unstyled:
        shot = d.shots / label / "unstyled.png"
        await d.iframe.screenshot(path=str(shot))
        result.screenshots.append(shot)
        result.failures += [f"{label}: the deck is not styled: {problem} [{shot}]"
                            for problem in sorted(set(d.refused)) + unstyled]
        return
    result.passed.append(f"{label}: the deck's own styles are in force in the frame")
    slides = await d.js(
        "() => [...document.querySelectorAll('#track > .slide')]"
        ".map(e => [e.id, e.classList.contains('decision')])", what=f"{label}: listing the slides")
    bad = 0
    voting_bad = 0
    for n, (slide_id, decision) in enumerate(slides, 1):
        await d.fit()
        await d.inner.click(f"#go-{n}")
        await d.inner.wait_for_selector(f"#{slide_id}.current")
        measured = await d.js(MEASURE, what=f"{label}: measuring slide {n}")
        voting = await d.js(VOTING, what=f"{label}: checking slide {n}'s voting")
        shot = d.shots / label / f"{n:02d}-{slide_id}.png"
        await d.iframe.screenshot(path=str(shot))
        result.screenshots.append(shot)
        for problem in measured["problems"]:
            bad += 1
            result.failures.append(f"{label} slide {n} ({slide_id}): {problem} [{shot}]")
        for problem in voting:
            voting_bad += 1
            result.failures.append(f"{label} slide {n} ({slide_id}) voting: {problem} [{shot}]")
    if not bad:
        result.passed.append(f"{label}: all {len(slides)} slides fit, text 13 to 25 px")
    if not voting_bad and d.deck.get("decisions"):
        result.passed.append(f"{label}: every vote control is visible, at least "
                             f"{MIN_CONTROL_PX} px tall and inside the frame, one Choose on "
                             f"each option, every option picture at least {MIN_PICTURE_PX} px")


async def _answer(d: Deck, question: str, option: str, by_click: bool) -> tuple[bool, str]:
    """Answer one question on the page (Choose, or Decide later) or by
    voice (decide.py through run_package_command): (ok, how)."""
    if by_click:
        selector = (f"#d-{question} .later" if option == "later"
                    else f"#opt-{question}-{option} .choose")
        await d.inner.click(selector)
        try:
            await d.inner.wait_for_function(
                "() => document.getElementById('status').textContent === 'Saved.'",
                timeout=15000)
        except Exception as e:                      # the save never came back
            return False, f"the page's click on {selector} did not save: {e}"
        return True, f"a click on {option} on the page"
    ok, said = await d.tool("run_package_command", package=d.package_id,
                            command=f'"$JARVIS_PYTHON" scripts/decide.py {question} {option}')
    return ok and "Recorded:" in said, f"decide.py {question} {option}: {said[:200]}"


async def _check_first_time(d: Deck) -> None:
    """From slide 1, as a first-time user: at each question the story waits
    (nothing more after longer than a beat), then an answer, by page and by
    voice in turn, moves it to the next question; every question that
    applies ends answered, in order."""
    result, story = d.result, d.parsed
    d.clear_answers()
    await d.reload()
    await d.js("() => { window.__shown = []; window.__rings = []; }", what="first time: setup")
    since = time.monotonic()
    ok, said = await d.tool("tell_package_story", package=d.package_id, slide=1)
    if not result.expect(ok, "first time: tell_package_story started from slide 1",
                         f"first time: tell_package_story said: {said}"):
        return
    asked: list[str] = []
    last_slide, n = 0, 0
    voice_shown = False
    while True:
        record = d.answers()
        upcoming = [s for s in story.open_questions(record) if s > last_slide]
        if not upcoming:
            break
        slide = upcoming[0]
        question = story.slides[slide - 1].waits
        frame = await d.story_frame(
            since, lambda f: f.get("state") in ("waiting", "finished", "stopped")
            and not (f.get("state") == "waiting" and f.get("slide", 0) <= last_slide),
            STORY_SEC)
        if frame is None or frame.get("question") != question:
            result.failures.append(
                f"first time: the story did not reach its wait for {question} (slide "
                f"{slide}) within {STORY_SEC}s; it said {frame}. {d.heard_so_far(since)}")
            await d.shoot(f"not-waiting-{question}", whole=True)
            return
        waited_at = time.monotonic()
        on = await d.current_slide()
        asking = await d.js(
            "(id) => document.getElementById(id).classList.contains('asking')", f"d-{question}",
            what=f"first time: reading whether {question}'s slide says it waits")
        await asyncio.sleep(WAIT_LONGER_SEC)
        later = [f for f in d.stories(waited_at) if f is not frame]
        moved = await d.current_slide()
        audio = d.cued_audio(waited_at + 0.2)
        shot = await d.shoot(f"waiting-{question}")
        if not result.expect(
                on == moved == f"d-{question}" and asking and not later and not audio,
                f"first time: the story waits at {question} (slide {slide}): nothing more "
                f"said or shown in {WAIT_LONGER_SEC:.0f}s",
                f"first time: at {question} the story did not wait: slide {on} -> {moved}, "
                f"asking shown {asking}, frames since {later}, {len(audio)} cued audio "
                f"[{shot}]"):
            return
        asked.append(question)
        by_click = n % 2 == 0
        decision = next(x for x in d.deck["decisions"] if x["id"] == question)
        option = "later" if n == 1 else decision["recommended"]
        since = time.monotonic()
        ok, how = await _answer(d, question, option, by_click)
        if not result.expect(ok, f"first time: answered {question} by {how}",
                             f"first time: answering {question} failed: {how}"):
            return
        if not by_click and not voice_shown:
            voice_shown = True
            wanted = "Recorded: Decide later" if option == "later" else "Recorded: "
            try:
                await d.inner.wait_for_function(
                    "([sel, want]) => document.querySelector(sel).textContent.startsWith(want)",
                    arg=[f"#d-{question} .recorded", wanted], timeout=FOLLOW_SEC * 1000)
                shown = True
            except Exception:
                shown = False
            shot = await d.shoot(f"voice-answer-{question}")
            result.expect(shown, f"first time: the spoken answer to {question} appeared on "
                                 f"its slide",
                          f"first time: the spoken answer to {question} never showed [{shot}]")
        elif by_click and n == 0:
            await d.shoot(f"chosen-{question}")
        last_slide, n = slide, n + 1
    end = await d.story_frame(since, lambda f: f.get("state") in ("finished", "stopped"),
                              STORY_SEC)
    record = d.answers()
    expected = [s.waits for s in story.slides if s.waits and story.applies(s, record)]
    result.expect(end is not None and end.get("state") == "finished",
                  "first time: the story went on to its end once the last was answered",
                  f"first time: the story ended {end}")
    result.expect(asked == expected and not story.open_questions(record),
                  f"first time: every question answered, in order ({', '.join(asked)})",
                  f"first time: asked {asked}, expected {expected}; open: "
                  f"{story.open_questions(record)}")
    rung = set(await d.js("() => window.__rings", what="first time: reading the rings"))
    told = {f.get("slide") for f in d.stories(0) if f.get("state") == "telling"}
    beats = [f"{n}.{b}" for n, s in enumerate(d.story["slides"], 1) if n in told
             for b, beat in enumerate(s["beats"], 1) if beat.get("targets")]
    unrung = [b for b in beats if b not in rung]
    result.expect(not unrung, "first time: every beat told pointed at its targets",
                  f"first time: story beats {', '.join(unrung)} pointed at nothing")
    await d.shoot("answers", whole=True)


async def _stopped_within(d: Deck, since: float, what: str) -> None:
    """The story says stopped, its cue is gone from the frame and no more of
    its words are sent, within STOP_WITHIN_SEC; the server says no story is
    running."""
    result = d.result
    frame = await d.story_frame(since, lambda f: f.get("state") == "stopped", STOP_WITHIN_SEC)
    deadline = since + STOP_WITHIN_SEC
    rings = await d.rings()
    while rings and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        rings = await d.rings()
    took = time.monotonic() - since
    await asyncio.sleep(1.0)
    stop_at = next((t for t, f in d.frames if t >= since and f.get("type") == "stop"), None)
    after = d.cued_audio(since + STOP_WITHIN_SEC)
    ok, status = await d.tool("tell_package_story", package=d.package_id, action="status")
    shot = await d.shoot(f"stopped-{what.replace(' ', '-')}")
    result.expect(frame is not None and not rings and not after and "No story is running" in status,
                  f"stop {what}: stopped within {STOP_WITHIN_SEC:.0f}s (cue gone in "
                  f"{took:.2f}s{', voice stopped' if stop_at else ''}), and the server says "
                  f"no story is running",
                  f"stop {what}: frame {frame}, {rings} rings left, {len(after)} more cued "
                  f"audio, status \"{status}\" [{shot}]")


async def _check_stop(d: Deck) -> None:
    """Stop during narration and while waiting, from the page's Stop and
    from the brain's tool; asked again, it is already stopped."""
    result, story = d.result, d.parsed
    first_question = next(n for n, s in enumerate(story.slides, 1) if s.waits)
    for how in ("the page", "the brain's tool"):
        for when in ("during narration", "while waiting"):
            d.clear_answers()
            since = time.monotonic()
            slide = 1 if when == "during narration" else first_question
            ok, said = await d.tool("tell_package_story", package=d.package_id, slide=slide)
            if not result.expect(ok, f"stop by {how} {when}: told from slide {slide}",
                                 f"stop by {how} {when}: tell said {said}"):
                return
            if when == "during narration":
                started = False
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not (d.cued_audio(since)
                                                           and await d.rings()):
                    await asyncio.sleep(0.05)
                started = bool(d.cued_audio(since))
            else:
                started = await d.story_frame(since, lambda f: f.get("state") == "waiting",
                                              STORY_SEC) is not None
            if not result.expect(started, f"stop by {how} {when}: the story was "
                                           f"{'speaking' if when == 'during narration' else 'waiting'}",
                                 f"stop by {how} {when}: the story never got there"):
                return
            await d.shoot(f"before-stop-{'page' if how == 'the page' else 'tool'}-"
                          f"{when.split()[0]}")
            at = time.monotonic()
            if how == "the page":
                await d.press_stop()
            else:
                ok, said = await d.tool("tell_package_story", package=d.package_id,
                                        action="stop")
                result.expect(ok and said.startswith("Stopped the story"),
                              f"stop by {how} {when}: the tool said it stopped",
                              f"stop by {how} {when}: the tool said {said}")
            await _stopped_within(d, at, f"by {how} {when}")
    ok, said = await d.tool("tell_package_story", package=d.package_id, action="stop")
    result.expect(ok and "already stopped" in said and "No story" not in said,
                  "stop again: the brain is told it is already stopped, never that there is "
                  "no story",
                  f"stop again said: {said}")


async def _check_resume(d: Deck) -> None:
    """After a Stop, resume picks up at the first question with no answer."""
    result, story = d.result, d.parsed
    d.clear_answers()
    first = next(n for n, s in enumerate(story.slides, 1) if s.waits)
    question = story.slides[first - 1].waits
    decision = next(x for x in d.deck["decisions"] if x["id"] == question)
    ok, how = await _answer(d, question, decision["recommended"], by_click=False)
    if not result.expect(ok, f"resume: answered {question} first", f"resume: {how}"):
        return
    await d.reload()
    since = time.monotonic()
    await d.tool("tell_package_story", package=d.package_id, slide=1)
    await d.story_frame(since, lambda f: f.get("state") == "telling", 20)
    await d.tool("tell_package_story", package=d.package_id, action="stop")
    open_ = story.open_questions(d.answers())
    if not open_:
        result.passed.append("resume: nothing left to ask after one answer")
        return
    want = open_[0]
    since = time.monotonic()
    ok, said = await d.tool("tell_package_story", package=d.package_id, action="resume")
    first_frame = await d.story_frame(since, lambda f: f.get("state") == "telling", 20)
    waiting = await d.story_frame(since, lambda f: f.get("state") == "waiting", STORY_SEC)
    shot = await d.shoot("resumed")
    result.expect(ok and f"from slide {want} " in said and first_frame is not None
                  and first_frame.get("slide") == want and waiting is not None
                  and waiting.get("question") == story.slides[want - 1].waits,
                  f"resume: picked up at slide {want}, the first question with no answer",
                  f"resume: said \"{said}\", first frame {first_frame}, waiting {waiting} "
                  f"[{shot}]")
    await d.tool("tell_package_story", package=d.package_id, action="stop")


async def _in_browser(jarvis: Isolated, deck: dict, story: dict,
                      package_id: str, shots: Path, result: Result) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            for n, (window, frame_size) in enumerate(SIZES):
                label = f"{frame_size[0]}x{frame_size[1]}"
                (shots / label).mkdir(parents=True, exist_ok=True)
                d = Deck(browser, jarvis, deck, story, package_id, shots, result)
                try:
                    if not await d.open(window, frame_size):
                        continue
                    await _check_layout(d, label)
                    if n == 0:
                        # The story's behaviour, once, at the larger size. A
                        # presentation (no decisions) has no question to wait
                        # at, stop at or resume from: it is told to the end.
                        # Each step goes on only if the one before passed,
                        # so a failure ends the check at once.
                        steps = [_check_first_time]
                        if deck.get("decisions"):
                            steps += [_check_stop, _check_resume]
                        for step in steps:
                            if result.failures:
                                break
                            await step(d)
                finally:
                    if result.failures:
                        await d.shoot(f"last-{label}", whole=True)
                    await d.close()
                if result.failures:
                    break
        finally:
            await browser.close()


async def _within_the_wall_clock(work, result: Result) -> None:
    """Run `work`, ended with a failure once CHECK_SEC have passed by the wall
    clock. The event loop's own clock stops while the Mac sleeps, so a
    timeout on it alone can wait for hours."""
    started = time.time()
    task = asyncio.ensure_future(work)
    while not task.done():
        await asyncio.wait({task}, timeout=1)
        if not task.done() and time.time() - started > CHECK_SEC:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            result.failures.append(
                f"The check did not finish within {CHECK_SEC // 60} minutes by the clock "
                f"(it ran {round(time.time() - started)}s; the Mac may have slept, or a step "
                f"stalled): ended.")
            return
    task.result()


@contextlib.contextmanager
def _awake():
    """The Mac kept from sleeping while the check runs: `caffeinate` (part of
    macOS) holds the assertions for as long as this process lives. A Mac that
    sleeps mid-check stops the stand-in voice's playback, the server's
    timers and the check's own, so a story never reaches its wait."""
    tool = shutil.which("caffeinate")
    held = None
    if tool:
        held = subprocess.Popen([tool, "-i", "-s", "-w", str(os.getpid())],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    try:
        yield
    finally:
        if held is not None and held.poll() is None:
            held.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                held.wait(5)


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


# -- the whole check --------------------------------------------------------------------

def check(archive: Path, deck: dict, folder: Path) -> Result:
    """Check the zip in an isolated JARVIS; screenshots go under `folder`.
    The Mac is kept awake while it runs (_awake)."""
    with _awake():
        return _check(archive, deck, folder)


def _check(archive: Path, deck: dict, folder: Path) -> Result:
    from jarvis.decision_package import deliver
    result = Result()
    folder.mkdir(parents=True, exist_ok=True)
    jarvis = None
    try:
        dist = built_page()
        jarvis = Isolated(Path(tempfile.mkdtemp(prefix="jarvis-decision-check-")),
                          deck.get("language", "en"), dist)
        # Stamped for the isolated JARVIS, which, like the live one, unpacks
        # a decision package only with a stamp for its own token.
        checked = folder / f"checked-{archive.name}"
        decision_stamp.stamp(archive, checked, jarvis.token)
        delivered = deliver(checked, jarvis.base, jarvis.token)
        package_id = delivered.package["id"]
        result.passed.append(f"unpacked in the isolated JARVIS as {package_id}")
        ok, said = jarvis.tool("list_packages", name=deck["title"])
        result.expect(ok and package_id in said,
                      f"list_packages found it by its title's words",
                      f"list_packages by \"{deck['title']}\" said: {said}")
        story = json.loads((jarvis.package_folder(package_id) / "story.json").read_text())
        asyncio.run(_within_the_wall_clock(
            _in_browser(jarvis, deck, story, package_id, folder, result), result))
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
