# Handoff and return

## Sender to author

The sender's handoff JSON gives only what the author cannot work out itself (`docs/making-a-package.md` defines it):

| Field | What it is |
|---|---|
| `question` | The question or decision for Tim, one line. |
| `work` | Its Work item id, or a list of them. |
| `evidence` | References the answer rests on: files, commits, PRs, CI runs, records. Relative paths are relative to the handoff file. |
| `decisions` | Optional: the decisions it asks, as decision objects. |
| `approval` | Architecture documents only: `true` asks Tim to approve the design. |

`purpose`, `conclusion`, `audience`, `questions` and any structure (`shape`, `slides`, `outline`, `order`, `situation`) are refused, each with its reason: the author works out the answer, what Tim knows and lacks, his questions and the order from its own research and prep. A complete-looking handoff is no reason to accept its claims.

## Author to sender

Return one result containing:

- **Readiness:** `ready-for-delivery`, `needs-evidence` or `needs-scope-decision`. These describe the package, not Tim's approval or the Work item's completion.
- **Artifacts:** the package source, its assets, prep.md, script.md, edits.md, and the production-guide path used.
- **What it enables:** what the package lets Tim understand or decide, with the decision keys and exactly what each answer authorizes.
- **Corrections:** material corrections to the sender's account, each with evidence; unknowns, whether they block a decision, and who can settle each.
- **What ran:** the editors' and judges' rounds, the check's output, and anything not tested.

The sender's command builds, proves and delivers the package with its own session, Work item and workflow, so his answers come back to it. A finished authoring turn does not complete the Work item.
