"""Author-declared package explanations and their creation-time check.

Only declared Markdown is read. Source material is neither parsed nor inferred.
The factory supplies its existing zip member list; this module does not discover it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from jarvis import bundle_archive, folder_readers, repo_read
from jarvis.folder_contract import FolderError


def declaration(value) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('understanding must map package members to author-written Markdown members.')
    for source, markdown in value.items():
        for name in (source, markdown):
            if not isinstance(name, str) or bundle_archive.normalised(name) != name:
                raise ValueError('Understanding paths must be plain relative package member paths.')
        if Path(markdown).suffix.lower() not in {'.md', '.markdown'}:
            raise ValueError('Understanding targets must be Markdown members.')
    return dict(value)


def documents(manifest) -> tuple[str, ...]:
    """The author's explicit content, in manifest order, without duplicate loads."""
    return tuple(dict.fromkeys([name for name in (manifest.get('brief'), manifest.get('skill')) if name]
                               + list(declaration(manifest.get('understanding')).values())))


def markdown_text(folder, name) -> str:
    declaration({'source': name})
    try:
        with folder_readers.opened_file(Path(folder), Path(name)) as (fd, info):
            if info.st_size > repo_read.READ_MAX_FILE_BYTES:
                raise ValueError(f'{name} exceeds the existing 4 MiB text-file read limit.')
            raw = os.pread(fd, info.st_size + 1, 0)
            if len(raw) != info.st_size:
                raise ValueError(f'{name} changed while its understanding was read.')
        text = raw.decode('utf-8-sig')
    except (FolderError, OSError, UnicodeError) as error:
        raise ValueError(f'{name} is not readable UTF-8 author Markdown ({type(error).__name__}).') from None
    if not text.strip() or any(ord(char) < 32 and char not in '\n\r\t' for char in text):
        raise ValueError(f'{name} contains no usable author Markdown text.')
    return text


def author_texts(folder, manifest):
    """The declared author view stays within the existing text-file read budget."""
    total = 0
    coverage = declaration(manifest.get('understanding'))
    for name in documents(manifest):
        covered = [member for member, document in coverage.items() if document == name]
        text = (("Author-declared members: " + ', '.join(covered) + '\n\n') if covered else '')
        text += markdown_text(folder, name)
        total += len(("# " + name + "\n" + text + "\n\n").encode('utf-8'))
        if total > repo_read.READ_MAX_FILE_BYTES:
            raise ValueError('Authored understanding exceeds the existing 4 MiB text-read limit in total.')
        yield name, text


def validate(folder, manifest, members) -> None:
    """Check exact factory coverage and actual text, without certifying its meaning."""
    coverage = declaration(manifest.get('understanding'))
    members = tuple(members)
    names = {path.relative_to(folder).as_posix() for path in members}
    for path in members:
        try:
            ordinary = folder_readers.kind_of(Path(folder), path.relative_to(folder)) == 'file'
        except FolderError:
            ordinary = False
        if not ordinary:
            raise ValueError('Package members must be ordinary files: ' + path.relative_to(folder).as_posix())
    authored = set(documents(manifest))
    missing = (set(coverage) | authored) - names
    if missing:
        raise ValueError('Understanding declares missing package members: ' + ', '.join(sorted(missing)))
    # Manifest metadata and the declared explanations explain themselves; no circular self-map.
    uncovered = names - {'package.json'} - authored - set(coverage)
    if uncovered:
        raise ValueError('Author understanding is missing for: ' + ', '.join(sorted(uncovered))
                         + '. Add member-to-Markdown entries in package.json understanding.')
    if not authored:
        raise ValueError('The package needs author-written Markdown understanding in brief, skill or understanding.')
    for _ in author_texts(folder, manifest):
        pass


def authored_picture_references(deck):
    described = set()
    for slide in deck.get('slides', []):
        for block in slide.get('blocks', []):
            picture = block.get('picture')
            if picture and block.get('alt'):
                described.add(picture if isinstance(picture, str) else picture['src'])
            for picture in block.get('pictures', []):
                if isinstance(picture, dict) and picture.get('src') and picture.get('alt'):
                    described.add(picture['src'])
    for question in deck.get('decisions', []):
        for option in question.get('options', []):
            if option.get('picture') and option.get('name') and option.get('says'):
                picture = option['picture']
                described.add(picture if isinstance(picture, str) else picture['src'])
    return described


def validate_deck_inputs(deck, template, source_dir):
    coverage = declaration(deck.get('understanding'))
    for name in dict.fromkeys(coverage.values()):
        markdown_text(source_dir, name)
    missing = set(template.pictures_of(deck)) - authored_picture_references(deck) - set(coverage)
    if missing:
        raise ValueError('Pictures need associated author text or understanding Markdown: ' + ', '.join(sorted(missing)))


def prepare_deck(folder, deck, template, source_dir) -> None:
    """The shared builder serializes the author's structured content, not arbitrary files."""
    validate_deck_inputs(deck, template, source_dir)
    coverage = declaration(deck.get('understanding'))
    authored = {}
    for name in dict.fromkeys(coverage.values()):
        authored[name] = markdown_text(source_dir, name)
    for name, text in authored.items():
        target = folder / name
        if target.exists():
            raise ValueError(f'Authored understanding {name} collides with a generated deck member.')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    name = 'UNDERSTANDING.md'
    number = 1
    while (folder / name).exists():
        number += 1
        name = f'UNDERSTANDING-{number}.md'
    lines = ['# ' + deck['title'], '',
             '## Author content',
             'The following is the author-supplied slide and decision content. Table headings and rows retain their structure.',
             '```json', json.dumps({k: v for k, v in deck.items() if k != 'understanding'}, ensure_ascii=False, indent=2), '```',
             '## Narration', 'Narration is listed separately from visible slide content.',
             '```json', json.dumps(template.story(deck), ensure_ascii=False, indent=2), '```',
             '## Technical assets',
             'index.html is the shared deck shell. deck.js handles slide navigation and choices; deck.css supplies layout. '
             'deck-data.js contains the author content above. story.json supplies the narration above. '
             'For a decision deck, SKILL.md explains answering and scripts/decide.py records the chosen answers in results/.', '']
    # Existing alt text and option name/says are author-provided picture references.
    # Their semantic adequacy is the author's responsibility; no pixels are interpreted.
    described = authored_picture_references(deck)
    (folder / name).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    manifest_path = folder / 'package.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['brief'] = name
    standard = set(template.FIXED) | {'deck-data.js', 'story.json'} | described
    manifest['understanding'] = {**{member: name for member in sorted(standard) if (folder / member).is_file()}, **coverage}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    validate(folder, manifest, template.zip_package.files(folder))
