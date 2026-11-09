# Codex item completion is not turn completion

The live acceptance snapshot showed active child threads with `awaiting_model` sourced from `codex_runtime:item/completed`. PR102's original native Claude ordering correction and shared-consumer refactor did not repair this Codex transition.

A read-only native event observation on 2026-11-09 captured the deployment author's child thread:

| Unix observation time | Native event | Item | Correct phase |
| --- | --- | --- | --- |
| 1794246739.043624 | item/started | reasoning | model_active |
| 1794246739.045736 | item/completed | reasoning | model_active |
| 1794246745.774274 | item/started | commandExecution | tool_wait |
| 1794246746.931381 | item/started | commandExecution | tool_wait |
| 1794246747.575724 | item/completed | commandExecution | tool_wait, another command remains |

The old reducer erased model activity at the reasoning completion, roughly seven seconds before the next tool began. The corrected reducer retains the observed model-request phase across model-item boundaries. It stops at tool execution, a final-answer boundary, a turn terminal, disconnection or evidence expiry. It never promotes runtime-active status to model activity.

Native thread metadata also contained `sleep` and `subAgentActivity`, previously missing from the wait-item set. These now participate in the same outstanding-tool tracking. Completing one of several tools does not clear the others. After the last tool ends, the phase is awaiting_model until another model boundary is observed; no inference is made from runtime liveness alone.

An initial active-status read cannot overwrite an already observed live model boundary. Status-only discovery remains unconfirmed. The evidence now records item type and turn ID without storing content, tool arguments or output.

This measures observed request phases, not exact backend token generation. The same canonical inventory publishes the result to every activity consumer.
