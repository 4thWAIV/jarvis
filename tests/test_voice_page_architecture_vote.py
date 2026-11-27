"""His vote on an architecture package, in the actual PackageView, from
copies of two packages he pressed it on (tests/fixtures/architecture/votes:
their files as received, the answers he gave kept beside them as
`<name>.recorded-decisions.json`). Each asks one question of its own and
then his approval of the design, which is the one vote (no `approve-package`
at Done). Pressing Approve records it for the revision shown and the control
keeps showing it recorded.

Every answer here is this test JARVIS's own, in its own data folder. Needs
the pinned kit asset (JARVIS_LANGUAGE_KIT_ASSET; the language-kit workflow)."""
import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from jarvis import (language_documents, package_inbox, records, work_packages)
from tests.package_cards import open_question
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_brain_page_parity import wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_language_documents import canonical_server  # noqa: F401

pytestmark = pytest.mark.page

VOTES = Path(__file__).parent / "fixtures" / "architecture" / "votes"
NAMES = ["audio-interception", "cef-shutdown"]


def unpacked(name: str) -> work_packages.Package:
    """The copy of one package, in this JARVIS's packages folder, with the
    checked revision its manifest names in the document store as it was
    when he pressed (the newest checked revision of its document)."""
    from jarvis import data_paths
    manifest = json.loads((VOTES / name / "package.json").read_text())
    folder = data_paths.packages_dir() / f"{manifest['name']}-{manifest['version']}-local{name[:3]}"
    shutil.copytree(VOTES / name, folder, ignore=shutil.ignore_patterns("results"))
    subject = manifest["communication"]
    check = json.loads((VOTES / name / "check.json").read_text())
    row = {"document_id": subject["document_id"], "revision": subject["revision"],
           "source_sha256": subject["source_revision"], "kit_digest": subject["kit_digest"],
           "check_json": json.dumps(check), "created_at": 0}
    records.atomic(lambda unit: unit.put(
        language_documents.path_of(subject["document_id"]),
        {"id": subject["document_id"], "work_id": "w", "latest": subject["revision"],
         "publications": [], "events": [row]}, change="revision", summary="seed"))
    return work_packages.find(folder.name)


@pytest.mark.parametrize("name", NAMES)
def test_the_recorded_approval_is_described_as_his_vote_for_the_revision(name, canonical_server):  # noqa: F811
    package = unpacked(name)
    recorded = json.loads((VOTES / f"{name}.recorded-decisions.json").read_text())
    work_packages.save_result(package, "decisions.json", json.dumps(recorded).encode())
    shown = package_inbox.page_vote(package)
    approval = next(q for q in shown["asks"] if q["id"] == "approve-architecture")
    assert approval["answer"] == "approve" and approval["stale"] is False
    assert shown["question"] is None, "his approval is the one vote: no second at Done"


SLOW_LIST_SEC = 6
SHOWN_WITHIN_MS = 2500


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.asyncio(loop_scope="session")
async def test_pressing_approve_shows_it_recorded_at_once_however_slow_the_package_list(
        name, page_playwright, canonical_server, tmp_path):  # noqa: F811
    """His library is hundreds of packages and describing them all takes
    seconds (measured: 2 to 3 s on his Mac, longer under load). Pressing
    Approve records it for the revision shown, and the card shows it
    recorded and takes the next press at once: it never waits on the list."""
    from tests import dashboard_page
    server, _published, _checked, base, tmp = canonical_server
    await asyncio.to_thread(dashboard_page.build_bundle)
    package = await asyncio.to_thread(unpacked, name)
    await asyncio.to_thread(package_inbox.adopt, package, "0" * 64, "test")
    async with page_playwright as playwright, httpx.AsyncClient(base_url=base, timeout=60) as http:
        browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.goto(base)
        for _ in range(400):
            if server.ui_clients.pages():
                break
            await asyncio.sleep(.05)
        shown = await http.post(f"/api/packages/{package.id}/page", headers={"Origin": base})
        assert shown.status_code == 200, shown.text
        approval = (await open_question(page, "approve-architecture")).locator('[data-kind="approval"]')
        await approval.wait_for()

        async def slowly(route):
            await asyncio.sleep(SLOW_LIST_SEC)
            await route.continue_()
        await page.route("**/api/packages", slowly)

        approve = approval.get_by_role("button", name="Approve")
        await approve.click()
        await approval.locator(".package-vote-recorded", has_text="Approved").wait_for(
            timeout=SHOWN_WITHIN_MS)
        assert await approve.get_attribute("aria-pressed") == "true"
        assert not await approve.is_disabled(), "the control takes the next press at once"
        record = json.loads(work_packages.read_result(package, "decisions.json"))
        assert record["approve-architecture"]["answer"] == "approve"
        assert record["approve-architecture"]["subject"] == package_inbox.subject_of(package)
        await browser.close()
