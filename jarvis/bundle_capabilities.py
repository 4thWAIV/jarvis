"""What this JARVIS can do with a received file, advertised on its node
update (team_api.node_update -> `attachments`), and how a recipient's
nodes are judged from the team server's snapshot.

The advertisement is versioned and bounded (messaging wire's
`AttachmentCapabilities`): per format, the operations the readers' health
check says work now (bundle_readers.health), the archive budgets, and the
work-package manifest versions and modes. A node whose record has none is
an older JARVIS: `legacy`, so unknown, never assumed able or unable.
"""
from __future__ import annotations

from typing import Literal, Optional

# A capability record older than this, or from a node that is not online,
# is not trusted for a prediction.
CAPABILITY_FRESH_SEC = 10 * 60

Standing = Literal["advertised", "legacy", "stale", "offline"]


def local_capabilities() -> dict:
    """This node's AttachmentCapabilities as wire JSON, for node_update."""
    raise NotImplementedError("transport lane")


def node_standing(node: dict, now_ms: Optional[int] = None) -> Standing:
    """How far one NodeView's attachment capabilities can be trusted."""
    raise NotImplementedError("transport lane")
