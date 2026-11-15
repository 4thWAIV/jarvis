"""Reproduce a signed isolated-package acceptance run; this never delivers live.

Run from the repository root with --session naming the existing signing session.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from jarvis import agent_keys, decision_check, make_package


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    presentation = make_package.is_presentation(args.source)
    made = (make_package.make_presentation if presentation else make_package.make_deck)(args.source, args.output)
    built = time.monotonic()
    folder = args.output / ('compiled' if presentation else '') / made.deck['name']
    copies = {'deck.js': Path('examples/packages/decision-deck/deck.js'),
              'deck.css': Path('examples/packages/decision-deck/deck.css')}
    if presentation:
        copies.update({name: Path('jarvis/presentation_assets') / name
                       for name in ('presentation.js', 'presentation.css')})
    renderer_hashes = {name: {'source': sha(source), 'compiled': sha(folder / name)}
                       for name, source in copies.items()}
    assert all(h['source'] == h['compiled'] for h in renderer_hashes.values()), renderer_hashes
    result = decision_check.check(made.archive, made.deck, args.output / 'production-check',
                                  agent_keys.signer_for(args.session), args.session)
    report = {'ok': result.ok, 'source_sha256': sha(args.source),
              'renderer_hashes': renderer_hashes, 'story_helper_sha256': sha(Path('jarvis/package_story.js')),
              'build_seconds': built - start, 'production_seconds': time.monotonic() - built,
              'passed': result.passed, 'failures': result.failures,
              'screenshots': [str(path) for path in result.screenshots]}
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'ok': result.ok, 'failures': result.failures}))
    if not result.ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
