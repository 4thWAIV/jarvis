"""An agent's canonical architecture document, from its YAML to a package in
Tim's inbox: the signed command (language_document.py), make_package
--document (JARVIS's projection of the exact checked revision around the
fresh author's explanation, author-proven and signed), its admission and
ground by package id. The kit-backed tests run where the pinned kit asset
is supplied (JARVIS_LANGUAGE_KIT_ASSET; the language-kit workflow)."""
import hashlib
import json
import zipfile

import pytest

from jarvis import (agent_key_store, agent_keys, agent_rights, author_proof, language_document,
                    language_document_api, language_projection, make_package, package_inbox,
                    session_watch, work_packages, workflow_report)
from jarvis import decision_package as dp
from tests import architecture_author
from tests.agent_key_fixtures import Agent
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)

SESSION = "sess-architect-0001"
ROUTE = language_document.ROUTE


@pytest.fixture
def through_the_route(wired, monkeypatch):  # noqa: F811
    """The agent commands' requests go to this test's JARVIS, signed by the
    agent's key exactly as they are sent (workflow_report._Client)."""
    def send_bytes(self, method, url, data, content_type):
        headers = {"Content-Type": content_type}
        if self.signer:
            headers["Authorization"] = agent_keys.request_header(
                self.signer[0], self.signer[1], method, url, data or b"")
        r = wired.client.request(method, url, content=data, headers=headers)
        return workflow_report._Response(r.status_code, r.content,
                                         r.headers.get("content-type", ""))
    monkeypatch.setattr(workflow_report._Client, "send_bytes", send_bytes)
    monkeypatch.setattr(workflow_report, "base_url", lambda: "http://testserver")
    return wired


def agent(rights: tuple[str, ...] | None = None) -> Agent:
    """This test's agent, onboarded, with the default rights or `rights`."""
    made = Agent(agent_keys._claude_home(), SESSION).onboarded()
    if rights is not None:
        agent_key_store._set_rights(agent_key_store.agent_principal(SESSION), rights,
                                    agent_rights.ANY_SCOPE, "test")
    return made


def collect(client, who: Agent, body: dict) -> dict:
    """One operation through the route as `who`, every page joined."""
    page = who.post(client, ROUTE, body).json()
    text = page["data"]
    while page["next"]:
        page = who.post(client, ROUTE, {"operation": "continue",
                                        "continuation": page["next"]}).json()
        text += page["data"]
    return json.loads(text)


def validate_arguments(draft) -> dict:  # noqa: F811
    """What an agent gives validate besides the YAML: no readable_markdown,
    since the fresh author writes the explanation."""
    return {k: draft[k] for k in ("work_id", "scope_revision", "author", "coverage")}


# -- the command ----------------------------------------------------------------------

def test_the_command_validates_and_retrieves_signed_with_the_agent_s_key(
        through_the_route, draft, tmp_path, capsys):  # noqa: F811
    agent()
    source = tmp_path / "architecture.yaml"
    source.write_text(draft["source"])
    assert language_document.main(["--as", SESSION, "validate", "--source", str(source),
                                   "--json", json.dumps(validate_arguments(draft))]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] and envelope["result"]["check"]["ok"]
    result = envelope["result"]
    assert result["metadata"]["reported_by"] == f"agent:{SESSION}"
    assert "readable" not in result["metadata"]
    arguments = tmp_path / "retrieve.json"
    arguments.write_text(json.dumps({k: result[k] for k in ("document_id", "revision")}))
    assert language_document.main(["--as", SESSION, "retrieve", "--json-file",
                                   str(arguments)]) == 0
    assert json.loads(capsys.readouterr().out) == envelope


def test_a_draft_that_does_not_check_is_kept_and_the_command_says_so(
        through_the_route, draft, tmp_path, capsys):  # noqa: F811
    agent()
    source = tmp_path / "architecture.yaml"
    source.write_text(draft["source"].replace("members: none", "members: pending"))
    assert language_document.main(["--as", SESSION, "validate", "--source", str(source),
                                   "--json", json.dumps(validate_arguments(draft))]) == 1
    out = capsys.readouterr()
    assert not json.loads(out.out)["result"]["check"]["ok"]
    assert "cannot be sent" in out.err


def test_without_the_packages_right_the_command_is_refused(through_the_route, capsys):
    agent(rights=("work",))
    assert language_document.main(["--as", SESSION, "capabilities"]) == 1
    assert "403" in capsys.readouterr().err


def test_an_agent_cannot_publish_through_the_route(through_the_route, draft):  # noqa: F811
    who = agent()
    checked = language_document_api.Adapter().documents.validate(**validate_arguments(draft),
                                                                 source=draft["source"])
    answer = collect(through_the_route.client, who, {"operation": "publish", "arguments": {
        k: checked[k] for k in ("document_id", "revision", "source_revision")}})
    assert not answer["ok"] and "make_package --document" in answer["error"]["cause"]


def test_a_continuation_belongs_to_the_caller_that_started_it(through_the_route, draft):  # noqa: F811
    who = agent()
    checked = language_document_api.Adapter().documents.validate(**validate_arguments(draft),
                                                                 source=draft["source"])
    first = who.post(through_the_route.client, ROUTE, {"operation": "retrieve", "arguments": {
        k: checked[k] for k in ("document_id", "revision")}}).json()
    assert first["next"]
    from jarvis import data_paths
    stolen = through_the_route.client.post(ROUTE, json={"operation": "continue",
                                                        "continuation": first["next"]},
                                           headers={"Authorization": "Bearer "
                                                    + data_paths.ensure_tool_token()}).json()
    assert "continuation is unavailable" in stolen["data"]


# -- the author's own check -----------------------------------------------------------

def test_the_author_s_check_takes_only_its_explanation(tmp_path, capsys):
    package = tmp_path / "package"
    package.mkdir()
    (package / "UNDERSTANDING.md").write_text("# The store\n\nOne input goes through it.\n")
    check = [str(package), "--check-only", "--document", "document-x", "--revision", "a" * 64]
    assert make_package.main(check) == 0
    out = capsys.readouterr().out
    assert "document-x" in out
    assert author_proof.MARK + author_proof.source_digest(package) in out
    (package / "index.html").write_text("<p>a page of the author's own</p>")
    assert make_package.main(check) == 1
    assert "holding only UNDERSTANDING.md" in capsys.readouterr().out
    (package / "index.html").unlink()
    (package / "UNDERSTANDING.md").write_text("   \n")
    assert make_package.main(check) == 1


def test_document_and_revision_go_together(tmp_path):
    with pytest.raises(SystemExit):
        make_package.main([str(tmp_path), "--check-only", "--document", "document-x"])


# -- make_package --document: authored, projected, signed, delivered, admitted -------------

@pytest.fixture
def factory(through_the_route, monkeypatch, tmp_path):
    """make_package's author is the stand-in claude (tests/architecture_author)
    and its delivery goes to this test's JARVIS, signed as it is sent."""
    monkeypatch.setenv("JARVIS_CLAUDE_PATH", str(architecture_author.install(tmp_path / "bin")))
    monkeypatch.setenv("AUTHOR_TEST_CLAUDE_ROOT", str(session_watch.config_roots()[0]))

    def post(url, token, body, timeout=60, signer=None):
        data = json.dumps(body).encode()
        r = through_the_route.client.post(url, content=data, headers={
            "Content-Type": "application/json",
            "Authorization": agent_keys.request_header(signer[0], signer[1], "POST", url, data)})
        if r.status_code != 200:
            raise OSError(f"{url} answered {r.status_code}: {r.text}")
        return r.json()
    monkeypatch.setattr(dp, "post", post)
    return through_the_route


def handoff(tmp_path):
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps({"purpose": "Explain the proposed document store",
                                "audience": "Tim, who decides whether it is built",
                                "questions": ["What does it own?"],
                                "evidence": ["the architecture document"]}))
    return path


def make(tmp_path, checked, *extra) -> int:
    return make_package.main(["--document", checked["document_id"], "--revision",
                              checked["revision"], "--handoff", str(handoff(tmp_path)),
                              "--engine", "claude", "--session", SESSION,
                              "--out", str(tmp_path / "out"),
                              "--deliver-to", "http://testserver", *extra])


def test_a_checked_document_reaches_the_inbox_author_proven_and_grounds(
        factory, draft, tmp_path, capsys):  # noqa: F811
    who = agent()
    checked = language_document_api.Adapter().documents.validate(**validate_arguments(draft),
                                                                 source=draft["source"])
    assert make(tmp_path, checked) == 0, capsys.readouterr().out
    record = json.loads((tmp_path / "out" / "delivery.json").read_text())
    assert record["work"] == draft["work_id"], "it goes on the document's own Work item"
    assert record["kind"] == make_package.ARCHITECTURE_KIND

    entry = package_inbox.get(record["inbox"])
    assert entry.from_session == SESSION, "his answers go back to the sending agent"
    package = work_packages.find(record["package"])
    canonical = json.loads(work_packages.page_file(package, "canonical.json"))
    assert {k: canonical[k] for k in ("document_id", "revision", "source_revision")} == {
        k: checked[k] for k in ("document_id", "revision", "source_revision")}
    authored = work_packages.page_file(package, "UNDERSTANDING.md")
    assert canonical["understanding"] == {"origin": language_projection.FRESH_AUTHOR,
                                          "member": "UNDERSTANDING.md",
                                          "markdown_sha256": hashlib.sha256(authored).hexdigest(),
                                          "purpose": "Explain the proposed document store"}
    assert package.manifest.purpose == "Explain the proposed document store", \
        "the package states the handoff's purpose"
    assert work_packages.page_file(package, "source.yaml").decode() == draft["source"]
    assert work_packages.page_file(package, "index.html") == open(
        checked["references"]["view"], "rb").read()
    with zipfile.ZipFile(record["zip"]) as z:
        names = z.namelist()
    assert any(n.endswith(".jarvis/author/package/UNDERSTANDING.md") for n in names)

    # Tim's JARVIS reads the explanation as it reads any package's.
    opened = _call(factory, "package_inbox", package=entry.id, understanding_offset=0)["text"]
    assert "One input goes through DocumentStore" in opened

    grounded = collect(factory.client, who, {"operation": "ground", "arguments": {
        "package_id": package.id, "inbox_id": entry.id, "declaration": "DocumentStore"}})
    assert grounded["ok"], grounded
    result = grounded["result"]
    assert {k: result[k] for k in ("document_id", "revision", "source_revision")} == {
        k: checked[k] for k in ("document_id", "revision", "source_revision")}
    assert result["source"]["text"] == draft["source"]
    assert result["understanding"]["text"] == authored.decode()
    assert result["understanding"]["origin"] == language_projection.FRESH_AUTHOR


AUTHORED_STORY = {"title": "The document store", "questions": [
    {"id": "ownership-clear", "question": "Is what the store owns clear?",
     "options": [{"id": "clear", "name": "Clear", "says": "Nothing to change."},
                 {"id": "unclear", "name": "Unclear", "says": "Explain it again."}]}],
    "slides": [{"title": "What it owns", "beats": [{"say": "The store owns its documents."}]},
               {"title": "Clear?", "waits": "ownership-clear",
                "beats": [{"say": "Is what it owns clear?"}]}]}


def test_the_author_s_story_is_composed_once_and_its_question_is_asked_in_the_head(
        factory, draft, tmp_path, capsys, monkeypatch):  # noqa: F811
    agent()
    story = tmp_path / "authored-story.json"
    story.write_text(json.dumps(AUTHORED_STORY))
    monkeypatch.setenv("AUTHOR_TEST_STORY", str(story))
    checked = language_document_api.Adapter().documents.validate(**validate_arguments(draft),
                                                                 source=draft["source"])
    assert make(tmp_path, checked) == 0, capsys.readouterr().out
    record = json.loads((tmp_path / "out" / "delivery.json").read_text())
    package = work_packages.find(record["package"])
    canonical = json.loads(work_packages.page_file(package, "canonical.json"))
    assert canonical["understanding"]["story_sha256"] == hashlib.sha256(
        story.read_bytes()).hexdigest()
    told = package.story()
    assert [s.waits for s in told.slides] == [None, "ownership-clear",
                                              package_inbox.UNDERSTOOD_ID], "one vote, last"
    with zipfile.ZipFile(record["zip"]) as z:
        names = [n for n in z.namelist() if "/.jarvis/author/" in n or n.startswith(".jarvis/author/")]
        assert sorted(n.split(".jarvis/author/")[1] for n in names) == [
            "package/UNDERSTANDING.md", "package/story.json"]
    vote = package_inbox.page_vote(package)
    assert vote["question"] == "understood"
    assert [q["id"] for q in vote["asks"]] == ["ownership-clear"]
    said = _call(factory, "answer_package_decision", package=record["inbox"],
                 decision="ownership-clear", option="unclear", words="Say who writes them.")["text"]
    assert said.startswith("Recorded."), said
    answers = json.loads(work_packages.read_result(package, "decisions.json"))
    assert answers["ownership-clear"]["answer"] == "unclear"
    assert "understood" not in answers, "the vote stays his to give"


def test_an_author_story_with_extra_files_or_the_vote_is_refused_at_its_own_check(
        tmp_path, capsys):
    package = tmp_path / "package"
    package.mkdir()
    (package / "UNDERSTANDING.md").write_text("# The store\n")
    check = [str(package), "--check-only", "--document", "document-x", "--revision", "a" * 64]
    (package / "story.json").write_text(json.dumps(AUTHORED_STORY))
    assert make_package.main(check) == 0
    assert "UNDERSTANDING.md and package/story.json" in capsys.readouterr().out
    (package / "story.json").write_text(json.dumps({**AUTHORED_STORY, "slides": [
        {"title": "Got it?", "waits": "understood", "beats": [{"say": "Got it?"}]}]}))
    assert make_package.main(check) == 1
    assert "reserved question" in capsys.readouterr().out
    (package / "story.json").write_text('{"title": "a", "title": "b", "slides": []}')
    assert make_package.main(check) == 1
    assert "twice in one object" in capsys.readouterr().out
    (package / "story.json").write_text(json.dumps(AUTHORED_STORY))
    (package / "notes.md").write_text("extra")
    assert make_package.main(check) == 1
    assert "optionally, story.json" in capsys.readouterr().out


def test_a_document_goes_only_on_its_own_work_item(factory, draft, tmp_path, capsys):  # noqa: F811
    agent()
    checked = language_document_api.Adapter().documents.validate(**validate_arguments(draft),
                                                                 source=draft["source"])
    assert make(tmp_path, checked, "--work", "work-another") == 1
    assert "send it there" in capsys.readouterr().out
    assert not (tmp_path / "out" / "author").exists(), "refused before any author starts"


def test_an_unchecked_draft_is_never_packaged(factory, draft, tmp_path, capsys):  # noqa: F811
    agent()
    pending = {**validate_arguments(draft),
               "source": draft["source"].replace("members: none", "members: pending")}
    checked = language_document_api.Adapter().documents.validate(**pending)
    assert make(tmp_path, checked) == 1
    assert "no validated render" in capsys.readouterr().out
