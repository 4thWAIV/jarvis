"""The knowledge engine: a document store, a typed graph, a keyword index and
a vector index in one supervised process of its own, outside SQLite.

The design is `docs/planning/work-package-bundles/design.md`, section 7;
the build order is `build-plan.md` beside it.

It holds no truth that exists nowhere else (I1): every document, edge,
posting and vector is derived from JARVIS's records, Markdown and the
folders the policy lets JARVIS read, and `python -m jarvis.knowledge
rebuild` makes it again. It opens no file outside its own directory (I2),
and it imports nothing from `jarvis.server`, so it starts fast and alone.

Run as `python -m jarvis.knowledge serve`.
"""

FORMAT_NAME = "jkn"
FORMAT_VERSION = 1
