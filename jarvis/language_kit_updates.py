"""Quiet, conditional release checks with backoff and last-working retention.

Only the configured publisher's named asset is fetched. GitHub's asset digest
detects corruption relative to its metadata; it is not a publisher signature.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import tempfile
import time
from pathlib import Path

import httpx

from jarvis import language_kit as kit

API = f"https://api.github.com/repos/{kit.REPOSITORY}"
CHECK_INTERVAL = 6 * 60 * 60


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
    return state


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
            try:
                kit.activate(candidate)
                state["compatibility_review_required"] = False
            except kit.KitError as error:
                # The staged version is retained for an explicit compatibility
                # review; existing documents and the active kit remain pinned.
                state["compatibility_review_required"] = True
                state["error"] = str(error)
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

    async def run(self) -> None:
        while True:
            if (kit.root() / "active.json").is_file():
                await asyncio.to_thread(self.check)
            await asyncio.sleep(60)


class Runtime:
    def __init__(self):
        self.task = None
        self.client = None

    def start(self):
        # Keep the scheduler alive so explicit adoption after server startup
        # takes effect. Its loop performs no checks or downloads until adopted.
        if self.task:
            return
        self.client = httpx.Client(trust_env=False)
        self.task = asyncio.create_task(Updater(self.client).run())

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
            result = kit.activate(kit.active(args.digest), compatibility_reviewed=True)
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
