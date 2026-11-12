"""The knowledge engine: a document store, a typed graph, a keyword index and
a vector index in one supervised process of its own, outside SQLite.

The design is `docs/planning/work-package-bundles/design.md`, section 7;
the build order is `build-plan.md` beside it.

It is moving to the UDS design (docs/planning/knowledge-engine/plan-uds.md):
items at paths with their versions (`items.py`, `uds.py`) are records of
their own, so invariant I1 no longer holds and `python -m jarvis.knowledge
rebuild` builds only indexes, vectors and cards again, never dropping a
document. It opens no file outside its own directory (I2),
and it imports nothing from `jarvis.server`, so it starts fast and alone.

Run as `python -m jarvis.knowledge serve`.
"""

FORMAT_NAME = "jkn"
FORMAT_VERSION = 1
