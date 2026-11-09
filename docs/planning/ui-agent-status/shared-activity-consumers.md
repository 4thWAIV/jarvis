# Shared worker activity and its consumers

## Authority

`worker_activity.current(evidence, now)` applies evidence expiry and produces `state`, internal `model_work`/`generating`, and the shared `turn_open`, `active` and `orb_state`. `worker_inventory.build` reconciles provider observations, identities and parent links, then finalizes each worker through that function. Its `counts` function computes each deduplicated scope once and includes the scope's `orb_state`.

`server._worker_inventory` publishes the result through `SnapshotReader`, carrying `snapshot_id` and `taken_at`. The UI, tools and Work adapters consume that publication. They do not reinterpret session activity, file write times or workflow status as model work. Reads at different times can have different snapshot IDs; those are different observations, not parallel state machines.

## Scope of each display

| Display | Canonical input | Scope |
| --- | --- | --- |
| Brain total and provider count | `total` / `providers` | Deduplicated workers, including main agents, children, managed runs and brain |
| Agent orb | `workers[].activity.orb_state` | That agent itself |
| Agent family count and expanded hierarchy | `workers[].counts` | Agent plus all descendants |
| Legacy child count field | `descendant_counts.active` | Descendants only |
| Conversation orb | Brain worker's `activity.orb_state` | Active provider's JARVIS brain; listening/speaking remain voice states |
| Workflow orb | `workflow_counts[id].orb_state` | Attached workers and descendants, deduplicated |
| Workflow child orb | Child worker's `activity.orb_state` | That child |
| Work owner orb | Worker activity or workflow scope | Named owner; shown separately from assignment status |
| Work live record | Exact worker activity and snapshot ID | Existing agent/child ownership lookup |
| Brain `list_workers` | Same inventory | Bounded, untrusted JSON pages preserve snapshot and summary |
| Brain `list_agents` | Canonical worker activity | Each registered agent |
| Denice provider orb and pulse rate | Same provider result via `/live-workers` | All open-turn workers on that provider; no extra brain count |

## Meaning and limits

The visible activity uses the existing main-orb behavior: thinking starts when a turn opens and ends when the turn completes. Tools and waits within that turn do not turn the orb idle. The visible count is the number of workers with an observed open turn. It does not claim instantaneous token generation. Missing turn evidence remains unknown, separately counted; runtime-active status alone does not create a turn.

Model-request phases, recent streamed output, tool activity and permission waits remain internal detail in the same snapshot. They no longer substitute a different visible activity rule for the main orb's turn behavior.

Work assignment status has a stricter existing rule. An open turn alone does not make its assignment Working. The owner orb shows the same whole-turn activity as other orbs, while assignment state remains unconfirmed unless exact-assignment generation is established. The pending `fix/work-generation` work at `84a3fb1` is not imported or replaced by this change.

## Removed duplicate derivations

Agent orbs no longer interpret non-idle session status as thinking. Workflow orbs no longer interpret running business status as thinking. Conversation thinking uses the canonical brain row. Work's live lookup no longer reads transcripts separately. The directory no longer calculates a separate recent-file child count. Denice no longer independently discovers Codex activity or counts working sessions for its pulse input.

## Denice delivery

The study integration is on `fix/denice-shared-activity`, based on the preserved study branch. Its existing review URL stays unchanged when the release owner applies the commit and restarts the review server. The application's shared snapshot must be deployed first. The existing pulse scheduler and transmission event routing are unchanged.
