"""Delivery requires inspection of the exact render, including reader attachments."""
import json
import zipfile

import pytest

from jarvis import author_proof, decision_check, decision_package as dp, package_author
from tests.author_fixtures import accept_visual_review


@pytest.fixture
def rendered(tmp_path):
    source = tmp_path / "content.json"
    source.write_text('{"title":"A diagram"}')
    archive = tmp_path / "built.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("package.json", '{"runs":"page","entry":"index.html"}')
        z.writestr("index.html", '<img src="diagram.svg">')
        z.writestr("diagram.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
        z.writestr("report.pdf", b"document supplied for human viewing")
    shot = tmp_path / "render.png"
    shot.write_bytes(b"actual renderer output")
    result = decision_check.Result(passed=["images loaded"], screenshots=[shot])
    return source, archive, result, tmp_path / "review"


async def inspect(rendered, change=lambda response, request: None):
    source, archive, result, folder = rendered

    async def turn(prompt):
        assert "SAME fresh author" in prompt and "EVERY screenshot" in prompt
        request = folder / "visual-request.json"
        response = accept_visual_review(request)
        if "report.pdf" in response["members"]:
            response["members"]["report.pdf"] = {
                "use": "reader", "notes": "Both document pages are readable.",
                "evidence": ["Existing document tool: report.pdf page 1", "report.pdf page 2"]}
        change(response, json.loads(request.read_text()))
        request.with_name("visual-response.json").write_text(json.dumps(response))

    return await package_author.review(source, archive, result, turn, folder)


@pytest.mark.asyncio
async def test_review_binds_rendered_template_bytes_and_attachment_pages(rendered):
    receipt = await inspect(rendered)
    assert receipt["archive_sha256"] == author_proof._sha(rendered[1])
    assert receipt["source_sha256"] == author_proof.source_digest(rendered[0])
    assert receipt["review"]["members"]["report.pdf"]["evidence"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["screenshot", "attachment", "page-evidence", "request"])
async def test_missing_inspections_cannot_accept(rendered, missing):
    def change(response, request):
        if missing == "screenshot":
            response["screenshots"] = []
        elif missing == "attachment":
            del response["members"]["report.pdf"]
        elif missing == "page-evidence":
            response["members"]["report.pdf"]["evidence"] = []
        else:
            response["request_sha256"] = "older build"
    with pytest.raises(dp.Refused):
        await inspect(rendered, change)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["source", "archive", "screenshot"])
async def test_edits_after_rendering_invalidate_acceptance(rendered, changed):
    source, archive, result, _ = rendered
    target = {"source": source, "archive": archive, "screenshot": result.screenshots[0]}[changed]
    def change(response, request):
        with target.open("ab") as out:
            out.write(b"changed")
    with pytest.raises(dp.Refused, match="changed after rendering"):
        await inspect(rendered, change)


@pytest.mark.asyncio
async def test_author_can_reject_internal_cropped_label_despite_machine_pass(rendered):
    def change(response, request):
        response.update(status="blocked", notes="The SVG clips the final words of its label.")
    receipt = await inspect(rendered, change)
    assert receipt["machine_passed"]
    assert receipt["review"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_machine_failure_cannot_be_overridden_by_author(rendered):
    rendered[2].failures.append("picture did not load")
    with pytest.raises(dp.Refused, match="picture did not load"):
        await inspect(rendered)


@pytest.mark.asyncio
async def test_no_visual_material_has_explicit_content_based_result(rendered):
    with zipfile.ZipFile(rendered[1], "w") as z:
        z.writestr("package.json", '{"runs":"page","entry":"index.html"}')
        z.writestr("index.html", '<p>Plain text only.</p>')
    def change(response, request):
        response.update(status="not-applicable", visual_material=False,
                        notes="This package has only text and support files.")
    receipt = await inspect(rendered, change)
    assert receipt["review"]["status"] == "not-applicable"
