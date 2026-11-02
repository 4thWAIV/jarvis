"""The one service that looks inside a file someone sent: what it is, what
an archive holds, a member's text a page at a time, a preview and a
rendered page. The page's bundle routes (bundle_api.py) and the brain's
bundle tools (server.py) both call it, and get the same shapes
(bundle_contract.py).

A file is named by (message id, attachment id) for the owner signed in now
(`Owner`), and reached only through messaging_files: `open` downloads and
verifies it once if it is not here (the owner's instruction to look);
every other call needs it already verified and re-checks its hash. A member
is named by its `member_id`, never by a path the caller gives.

Read-only inspection never extracts into the packages folder, never clears
the quarantine mark, keeps no execute bit, runs no script, loads no macro
and executes no HTML. A runnable work package is recognised and badged
(work_packages.recognise); opening and running it stays open_package's.

Each outcome is handed to `observer` (bundle_status.py turns them into the
openability record the sender hears about); the service itself sends
nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
from uuid import UUID

from jarvis import bundle_archive
from jarvis.bundle_cache import DerivedCache
from jarvis.bundle_contract import (MEMBER_PAGE_DEFAULT, TEXT_PAGE_DEFAULT_CHARS,
                                    Classification, MemberPage, Operation, Preview, Reason,
                                    RenderedPage, RenderOptions, Support, TextPage)


@dataclass(frozen=True)
class Owner:
    """Whose messaging: the files service's namespace."""
    server_id: str
    owner_github_id: int


@dataclass(frozen=True)
class AttachmentKey:
    message_id: UUID
    attachment_id: UUID


@dataclass(frozen=True)
class Observation:
    """One inspection outcome: which operation on which member (None for
    the attachment as a whole) and how it went."""
    owner: Owner
    key: AttachmentKey
    attachment_sha256: str
    operation: Operation
    member_id: Optional[str]
    support: Support
    reason: Optional[Reason] = None


class BundleInspection:
    def __init__(self, files, cache: DerivedCache, *,
                 observer: Callable[[Observation], None] = lambda o: None,
                 budgets: bundle_archive.Budgets = bundle_archive.DEFAULT_BUDGETS):
        """`files` is messaging_files.FileSnapshots."""
        self.files, self.cache, self.observer, self.budgets = files, cache, observer, budgets

    async def open(self, owner: Owner, key: AttachmentKey) -> Classification:
        """Download and verify it if it is not here, then classify it."""
        raise NotImplementedError("readers lane")

    def classify(self, owner: Owner, key: AttachmentKey) -> Classification:
        raise NotImplementedError("readers lane")

    def list_members(self, owner: Owner, key: AttachmentKey, folder: str = "",
                     continuation: Optional[str] = None,
                     limit: int = MEMBER_PAGE_DEFAULT) -> MemberPage:
        """One folder of an archive, a page at a time ("" is the top, the
        wrapper folder included as a folder)."""
        raise NotImplementedError("readers lane")

    def read_member(self, owner: Owner, key: AttachmentKey, member_id: str,
                    continuation: Optional[str] = None,
                    max_chars: int = TEXT_PAGE_DEFAULT_CHARS) -> TextPage:
        raise NotImplementedError("readers lane")

    def preview_member(self, owner: Owner, key: AttachmentKey, member_id: str,
                       continuation: Optional[str] = None) -> Preview:
        raise NotImplementedError("readers lane")

    def render_page(self, owner: Owner, key: AttachmentKey, member_id: str, page: int,
                    options: RenderOptions = RenderOptions()) -> RenderedPage:
        """A PDF page (or an image, page 1) as PNG."""
        raise NotImplementedError("readers lane")

    def html_asset(self, owner: Owner, key: AttachmentKey, member_id: str,
                   relative: str) -> tuple[bytes, str]:
        """A static HTML preview's own file or one it names relative to it,
        inside the same archive only: (bytes, media type)."""
        raise NotImplementedError("readers lane")
