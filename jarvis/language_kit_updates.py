"""Quiet, conditional release checks with backoff and last-working retention.

Only the configured publisher's named asset is fetched. GitHub's asset digest
detects corruption relative to its metadata; it is not a publisher signature.

A staged candidate whose activation is refused (its language files changed,
or it was held after a rollback) is `held` in updates.json: its version,
digest, why and the files that changed. While it is held it is one silent
attention item (`attend`, kind `language_kit`, one per candidate digest),
resolved once the candidate is active or no longer held. Settings, Updates
shows it with Activate, his click (`POST /api/language-kits/activate`), and
the brain's acting `activate_language_kit` calls the same `approve_held`.
Every activation clears the stale refusal from updates.json.

A candidate the updater activates by itself (its language files the same
as the kit in force's) is never unseen: updates.json keeps it as
`activated` (from which version and digest to which, and when), and while
it is the kit in force it is one silent attention item of the same kind,
event `activated`, source the new kit's digest, kept until a later
activation replaces it or he marks it seen. His own activation of a held
candidate is his act and clears it.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

import httpx

from jarvis import language_kit as kit

API = f"https://api.github.com/repos/{kit.REPOSITORY}"
CHECK_INTERVAL = 6 * 60 * 60
log = logging.getLogger(__name__)

# The attention item a held candidate is (attention_store.py), one per
# candidate digest. Its events: `review`, the language files changed and
# his compatibility review is wanted; `refused`, activation refused for any
# other reason (held after a rollback, a kit file that changed).
KIND = "language_kit"
REVIEW, REFUSED = "review", "refused"
# The event of the item an automatic activation is: which version replaced
# which, the language unchanged.
ACTIVATED = "activated"


class Attention(Protocol):
    """The attention record, as far as this needs it."""

    def record(self, kind: str, source_id: str, event: str, **kw) -> object: ...

    def resolve(self, kind: str, source_id: str) -> bool: ...

    def open_sources(self, kind: str) -> list[str]: ...


def status() -> dict:
    try:
        state = json.loads((kit.root() / "updates.json").read_text())
        if not isinstance(state, dict):
            raise ValueError("The update status must be one object.")
    except (OSError, ValueError) as error:
        state = {"last_check": None, "next_check": 0, "failures": 0,
                 **({"error": f"Update status cannot be read: {error}"} if not isinstance(error, FileNotFoundError) else {})}
    state["adopted"] = (kit.root() / "active.json").is_file()
    try:
        selected = kit.active()
        state["active"] = {"version": selected.release.version, "digest": selected.release.digest}
        state.pop("active_error", None)
    except kit.KitError as error:
        state["active"] = None
        state["active_error"] = str(error)
    if "held" not in state and state.get("compatibility_review_required"):
        # Written before holds were kept: the refusal is the candidate's.
        state["held"] = _legacy_hold(state)
    hold = state.get("held")
    active_digest = (state["active"] or {}).get("digest")
    if not isinstance(hold, dict) or not hold.get("digest") or active_digest == hold["digest"]:
        # Nothing held, or the candidate is active now (activated from
        # anywhere): the review flag and the refusal's words went with it.
        if isinstance(hold, dict) and state.get("error") == hold.get("detail"):
            state["error"] = None
        state["held"] = None
        state["compatibility_review_required"] = False
    else:
        state["compatibility_review_required"] = True
    shift = state.get("activated")
    if not isinstance(shift, dict) or (shift.get("to") or {}).get("digest") != active_digest:
        # No automatic activation, or the kit it brought is no longer the
        # one in force (rolled back, or activated over since).
        state["activated"] = None
    return state


def _legacy_hold(state: dict) -> Optional[dict]:
    """The hold an older updates.json meant by `compatibility_review_required`:
    its `candidate`, with the language files that differ from the active
    kit's worked out from both kits on disk. None when that cannot be read."""
    candidate = state.get("candidate")
    if not isinstance(candidate, dict) or not candidate.get("digest"):
        return None
    try:
        staged = kit.active(str(candidate["digest"]))
        working = kit.active()
    except kit.KitError:
        return None
    changed = [name for name in kit.LANGUAGE_FILES
               if working.record["files"].get(name) != staged.record["files"].get(name)]
    error = kit.ReviewRequired(changed) if changed else kit.KitError(
        state.get("error") or "Activation was refused; review it before activating.")
    return _hold(staged.release, error, None)


def held() -> Optional[dict]:
    """The candidate held back from activation, or None."""
    return status()["held"]


def _hold(release: kit.Release, error: kit.KitError, before: Optional[dict]) -> dict:
    """What updates.json keeps of a candidate whose activation was refused;
    `since` is when it was first held."""
    review = isinstance(error, kit.ReviewRequired)
    same = isinstance(before, dict) and before.get("digest") == release.digest
    return {"version": release.version, "digest": release.digest,
            "reason": REVIEW if review else REFUSED,
            "changed": error.changed if review else [], "detail": str(error),
            "since": before.get("since") if same else time.time()}


def _settled(state: dict) -> None:
    """A candidate became active: the refusal recorded before is stale."""
    state.update(held=None, compatibility_review_required=False, error=None)


def activate_reviewed(digest: str) -> dict:
    """Activate a staged version after its compatibility review (the
    command's `activate-reviewed`, and `approve_held`). Clears the refusal
    recorded for it in updates.json and resolves its attention item."""
    selected = kit.activate(kit.active(digest), compatibility_reviewed=True)
    state = status()
    if state.get("held") is None:
        _settled(state)
    state["activated"] = None   # his act, not one he has to be told of
    kit._atomic_json(kit.root() / "updates.json", state)
    runtime.attend()
    return selected


def approve_held(digest: str) -> dict:
    """His approval of the held candidate (Settings, Updates' Activate and
    the brain's `activate_language_kit`): activate exactly the candidate
    held, by its digest. Returns the status after."""
    hold = held()
    if hold is None:
        raise kit.KitError("No language kit is waiting for review.")
    if digest != hold["digest"]:
        raise kit.KitError(f"The language kit waiting for review is {hold['version']} "
                           f"(digest {hold['digest']}); nothing was activated.")
    activate_reviewed(digest)
    return status()


def facts(hold: dict) -> dict:
    """A held candidate's attention facts: JARVIS's own and the trusted
    publisher's words, the same on every check so nothing new is recorded."""
    out = {"version": hold["version"], "digest": hold["digest"],
           "files": ", ".join(hold.get("changed") or [])}
    if hold.get("reason") != REVIEW:
        out["reason"] = hold.get("detail") or ""
    return out


def activation_facts(shift: dict) -> dict:
    """An automatic activation's attention facts: from which version to
    which (the publisher's tags, validated as release tags)."""
    return {"from": str((shift.get("from") or {}).get("version") or ""),
            "version": str(shift["to"]["version"]), "digest": str(shift["to"]["digest"])}


def attend(attention: Attention) -> None:
    """The held candidate as one silent attention item (one per digest),
    the automatic activation of the kit in force as another, and every
    other kit item resolved: active now or no longer held, or an
    activation replaced."""
    state = status()
    hold, shift = state["held"], state["activated"]
    keep = set()
    if hold is not None:
        attention.record(KIND, hold["digest"], hold["reason"], facts=facts(hold), audible=False)
        keep.add(hold["digest"])
    if shift is not None:
        attention.record(KIND, shift["to"]["digest"], ACTIVATED,
                         facts=activation_facts(shift), audible=False)
        keep.add(shift["to"]["digest"])
    for source in attention.open_sources(KIND):
        if source not in keep:
            attention.resolve(KIND, source)


class Updater:
    """A caller-owned client supplies authentication through existing policy."""
    def __init__(self, client: httpx.Client):
        self.client = client

    def _json(self, url: str) -> dict:
        response = self.client.get(url, timeout=20)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise kit.KitError("GitHub returned malformed object metadata.")
        return value

    def check(self, *, force: bool = False) -> dict:
        state = status()
        now = time.time()
        if not force and now < state.get("next_check", 0):
            return state
        state.update(last_check=now, next_check=now + CHECK_INTERVAL, error=None)
        try:
            headers = {"If-None-Match": state["etag"]} if state.get("etag") else {}
            initial = state.get("active") is None
            endpoint = f"{API}/releases/tags/{kit.PINNED_VERSION}" if initial else f"{API}/releases?per_page=20"
            response = self.client.get(endpoint, headers=headers, timeout=20)
            if response.status_code == 304:
                state["failures"] = 0
                return state
            response.raise_for_status()
            releases = [response.json()] if initial else response.json()
            if not isinstance(releases, list) or not all(isinstance(r, dict) for r in releases):
                raise kit.KitError("GitHub returned malformed release metadata.")
            # The release channel is explicitly the newest published non-draft
            # pre-release, never the stable release endpoint's different policy.
            eligible = [r for r in releases if r.get("prerelease") and not r.get("draft")]
            if not eligible:
                raise kit.KitError("The configured pre-release channel has no published candidate.")
            release = max(eligible, key=lambda r: r["published_at"])
            tag = release["tag_name"]
            # Validate before putting an untrusted tag into another URL.
            kit.Release(tag, "0" * 40, "0" * 64, tuple(kit.REQUIRED_CHECKS)).validate()
            carried = release.get("assets", [])
            if not isinstance(carried, list) or not all(isinstance(a, dict) for a in carried):
                raise kit.KitError("GitHub returned malformed asset metadata.")
            assets = [a for a in carried if a.get("name") == kit.ASSET]
            if len(assets) != 1:
                raise kit.KitError("The release does not carry exactly one evolang-kit.zip asset.")
            asset = assets[0]
            digest = str(asset.get("digest") or "").removeprefix("sha256:")
            commit = self._json(f"{API}/commits/{tag}")["sha"]
            checks = self._json(f"{API}/commits/{commit}/check-runs?per_page=100")["check_runs"]
            if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
                raise kit.KitError("GitHub returned malformed check-run metadata.")
            latest = {}
            for check in checks:
                if check.get("id", 0) >= latest.get(check.get("name"), {}).get("id", -1):
                    latest[check.get("name")] = check
            passed = tuple(c["name"] for c in latest.values() if c.get("status") == "completed"
                           and c.get("conclusion") == "success")
            pinned = kit.Release(tag, commit, digest, passed)
            pinned.validate()
            if state.get("active") is None and (tag, commit, digest) != (kit.PINNED_VERSION, kit.PINNED_COMMIT, kit.PINNED_DIGEST):
                raise kit.KitError("Initial adoption must use the approved pinned release; install that release before checking channel updates.")
            url = asset["browser_download_url"]
            expected = f"https://github.com/{kit.REPOSITORY}/releases/download/{tag}/{kit.ASSET}"
            if url != expected or asset.get("size", 0) > 8 * 1024 * 1024:
                raise kit.KitError("The candidate asset URL or size is outside the release policy.")
            with tempfile.TemporaryDirectory(prefix="language-kit-download-") as name:
                path = Path(name) / kit.ASSET
                size = 0
                with self.client.stream("GET", url, follow_redirects=True, timeout=30) as download:
                    download.raise_for_status()
                    with path.open("wb") as output:
                        for chunk in download.iter_bytes():
                            size += len(chunk)
                            if size > 8 * 1024 * 1024:
                                raise kit.KitError("The kit download exceeded its size limit.")
                            output.write(chunk)
                if size != asset["size"]:
                    raise kit.KitError("The kit download size differs from release metadata.")
                candidate = kit.stage(path, pinned)
            state["candidate"] = {"version": tag, "digest": digest, "commit": commit}
            before = state.get("active")
            try:
                kit.activate(candidate)
                _settled(state)
                if before and before.get("digest") != candidate.release.digest:
                    # Activated by itself: said, as one silent update.
                    state["activated"] = {
                        "from": {"version": before.get("version"), "digest": before.get("digest")},
                        "to": {"version": candidate.release.version,
                               "digest": candidate.release.digest},
                        "at": time.time()}
            except kit.KitError as error:
                # The staged version is retained for an explicit compatibility
                # review; existing documents and the active kit remain pinned.
                state["compatibility_review_required"] = True
                state["error"] = str(error)
                state["held"] = _hold(candidate.release, error, state.get("held"))
            state.update(etag=response.headers.get("etag"), failures=0)
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as error:
            failures = state.get("failures", 0) + 1
            state.update(error=str(error), failures=failures,
                         next_check=now + min(CHECK_INTERVAL, 60 * 2 ** min(failures, 8)))
            # Do not retain an ETag for an unaccepted candidate: a 304 must
            # never suppress retry after failed download, CI or smoke checks.
            state.pop("etag", None)
        finally:
            kit._atomic_json(kit.root() / "updates.json", state)
        return status()

    async def run(self, after: Callable[[], None] = lambda: None) -> None:
        """Check when due, then `after` (the runtime's `attend`), every
        minute once a kit is adopted; an activation from the command line
        is seen within the minute."""
        while True:
            if (kit.root() / "active.json").is_file():
                await asyncio.to_thread(self.check)
                await asyncio.to_thread(after)
            await asyncio.sleep(60)


class Runtime:
    def __init__(self):
        self.task = None
        self.client = None
        self.attention: Callable[[], Optional[Attention]] = lambda: None

    def start(self, attention: Optional[Callable[[], Optional[Attention]]] = None):
        # Keep the scheduler alive so explicit adoption after server startup
        # takes effect. Its loop performs no checks or downloads until adopted.
        if attention is not None:
            self.attention = attention
        if self.task:
            return
        self.client = httpx.Client(trust_env=False)
        self.task = asyncio.create_task(Updater(self.client).run(self.attend))

    def attend(self) -> None:
        """`attend` on the server's attention record; nothing outside a
        server (the command). Never raises."""
        attention = self.attention()
        if attention is None:
            return
        try:
            attend(attention)
        except Exception:
            log.warning("the held language kit's attention item was not recorded", exc_info=True)

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        if self.client:
            self.client.close()
            self.client = None


runtime = Runtime()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Adopt the approved kit, inspect updates, or restore a working version.")
    parser.add_argument("action", choices=("adopt-pinned", "check", "status", "rollback", "activate-reviewed"))
    parser.add_argument("--digest", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "rollback":
            result = kit.rollback()
        elif args.action == "activate-reviewed":
            if not args.digest:
                parser.error("activate-reviewed requires --digest from the compatibility review")
            result = activate_reviewed(args.digest)
        elif args.action == "status":
            result = status()
        else:
            if args.action == "adopt-pinned" and (kit.root() / "active.json").exists():
                raise kit.KitError("A kit is already adopted; use check to inspect the configured update channel.")
            with httpx.Client(trust_env=False) as client:
                result = Updater(client).check(force=True)
            if result.get("error"):
                print(json.dumps(result, indent=2))
                return 1
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error), "next_step": "Restore the last working kit or inspect the candidate before retrying."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
