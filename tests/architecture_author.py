"""A stand-in `claude` that authors an architecture document's explanation
the way the real fresh author is told to (package_author._assignment): it
reads the document folder the prompt names, writes package/UNDERSTANDING.md
(and package/story.json, copied from `AUTHOR_TEST_STORY` when it is set),
runs the exact check command the prompt gives as a real subprocess, and
records that call and its real output in its own transcript under
`AUTHOR_TEST_CLAUDE_ROOT` (which the receiver reads, author_proof.py).

Built from tests/fixtures/fake_brain.py, as test_package_author's engines are.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE_BRAIN = Path(__file__).parent / "fixtures" / "fake_brain.py"

ACTION = '''
    if '--check-only --document' in text:
        import json as _json, re as _re, subprocess as _sp, time as _time
        from pathlib import Path as _P
        _sys = __import__('sys')
        _sys.path.insert(0, {root!r})
        from jarvis import session_watch as _sw
        from tests.author_fixtures import lines as _lines
        _cmd = _re.search(r"exact command: (.*? --revision [0-9a-f]{{64}})", text).group(1)
        _doc = _P(_re.search(r"Its exact revision is in (\\S+?): source.yaml", text).group(1))
        _meta = _json.loads((_doc / 'document.json').read_text())
        _P('package').mkdir(exist_ok=True)
        _P('package/UNDERSTANDING.md').write_text(
            '# ' + _meta['title'] + '\\n\\n'
            + 'One input goes through ' + ', '.join(_meta['declarations']) + '. '
            + 'Each declaration is described in source.yaml and drawn on the page. '
            + 'Outside the language: ' + '; '.join(_meta['coverage']['outside_language']) + '\\n')
        if os.environ.get('AUTHOR_TEST_STORY'):
            _P('package/story.json').write_bytes(_P(os.environ['AUTHOR_TEST_STORY']).read_bytes())
        _run = _sp.run(['/bin/sh', '-c', _cmd], capture_output=True, text=True)
        _P('check-output.txt').write_text(_run.stdout + _run.stderr)
        _at = (_P(os.environ['AUTHOR_TEST_CLAUDE_ROOT']) / 'projects'
               / _sw.encode_cwd(os.getcwd()) / (SESSION_ID + '.jsonl'))
        _at.parent.mkdir(parents=True, exist_ok=True)
        _at.write_text(''.join(_json.dumps(o) + '\\n' for o in _lines(
            'claude', SESSION_ID, '', _time.time(), prompt=text,
            printed=_run.stdout.strip(), ran=_cmd)))
'''


def install(folder: Path, session: str = "fake-author-0001") -> Path:
    """The stand-in `claude` in `folder`, reporting `session` as its own."""
    folder.mkdir(parents=True, exist_ok=True)
    original = FAKE_BRAIN.read_text()
    start = "def reply(text):"
    fake = folder / "claude"
    fake.write_text(original.replace("#!/usr/bin/env python3", "#!" + sys.executable)
                    .replace('SESSION_ID = "fake-session-0001"', f"SESSION_ID = {session!r}")
                    .replace(start, start + ACTION.format(root=str(ROOT))))
    fake.chmod(0o755)
    return fake
