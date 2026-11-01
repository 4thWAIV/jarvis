// GENERATED from jarvis/ui_contract/ by scripts/generate_ui_contract.py.
// Do not edit: change the declarations there and regenerate. The test suite
// checks this copy is exact.

/** The whole contract: protocol and every family, merged and checked. */
export const UI_CONTRACT = {
  "protocol": "jarvis.ui",
  "version": 1,
  "socket": "/ws/voice",
  "fromBrowser": [
    "ui_hello",
    "ui_event",
    "ui_result"
  ],
  "fromServer": [
    "ui_welcome",
    "ui_request"
  ],
  "regions": [
    "left",
    "right",
    "main",
    "overlay",
    "footer"
  ],
  "actors": [
    "human",
    "brain"
  ],
  "operations": [
    "map",
    "navigate",
    "indicate",
    "clear",
    "journey"
  ],
  "statuses": [
    "completed",
    "not_found",
    "ambiguous",
    "not_visible",
    "blocked",
    "unavailable",
    "stale",
    "cancelled",
    "timeout",
    "busy",
    "expired",
    "invalid"
  ],
  "lifecycle": [
    "resolving",
    "revealing",
    "emitting",
    "travelling",
    "arrived",
    "holding",
    "fading",
    "cleared",
    "cancelled"
  ],
  "arrivals": [
    "animated",
    "static-reduced-motion"
  ],
  "endpointStyles": [
    "outline",
    "circle",
    "brackets"
  ],
  "revealModes": [
    "never",
    "navigate"
  ],
  "visibility": [
    "visible",
    "offscreen",
    "hidden",
    "occluded",
    "unmounted"
  ],
  "viewKinds": [
    "fixed",
    "tab",
    "detail",
    "modal",
    "menu"
  ],
  "availabilityReasons": {
    "no-entity": "Nothing of that kind is open or listed.",
    "entity-gone": "That item is no longer listed.",
    "not-loaded": "That item is not loaded on this page yet.",
    "not-mounted": "That view is not on the page.",
    "hidden": "That view is on the page but hidden.",
    "occluded": "Something covers it.",
    "offscreen": "It is scrolled out of view.",
    "no-route": "No registered pipeline path reaches it.",
    "no-adapter": "That part has no registered geometry yet.",
    "not-signed-in": "This needs the team server, which is not signed in.",
    "actor-not-allowed": "That command is not offered to this caller.",
    "source-busy": "The orb is carrying a real transmission.",
    "quiet-page": "The page is quiet, so no animation is drawn."
  },
  "timing": {
    "emitMs": 220,
    "travelMs": 1400,
    "pulseCount": 3,
    "pulseOffsetMs": 170,
    "arrivalAtMs": 1620,
    "ringGapMs": 350,
    "holdUntilMs": 4400,
    "resetByMs": 5000,
    "maxExtraHoldMs": 6000,
    "ringMs": 700
  },
  "limits": {
    "requestTtlMs": 15000,
    "answerGraceMs": 1000,
    "revealWaitMs": 4000,
    "sourceQueueMs": 3000,
    "mapMaxTargets": 200,
    "journeyMaxSteps": 6,
    "labelMax": 80,
    "idMax": 64,
    "keyMax": 512
  },
  "entityKeys": {
    "node": "^[A-Za-z0-9-]{1,64}$",
    "server": "^server$",
    "session": "^[A-Za-z0-9._:-]{1,128}$",
    "workflow": "^[A-Za-z0-9._:-]{1,128}$",
    "subagent": "^[A-Za-z0-9._:-]{1,256}$",
    "workflowItem": "^[A-Za-z0-9._:-]{1,128}$",
    "thread": "^[A-Za-z0-9-]{1,64}$",
    "message": "^[A-Za-z0-9-]{1,64}$",
    "attachment": "^[A-Za-z0-9-]{1,64}$",
    "folder": "^[A-Za-z0-9-]{1,64}$",
    "folderEntry": "^[^\\u0000]{1,512}$",
    "candidate": "^[A-Za-z0-9-]{1,64}$",
    "subsystem": "^[a-z0-9_-]{1,64}$",
    "provider": "^[a-z0-9_-]{1,32}$",
    "capture": "^[0-9]{1,12}$",
    "record": "^[A-Za-z0-9._:-]{1,128}$",
    "incident": "^[A-Za-z0-9._:-]{1,128}$",
    "check": "^[A-Za-z0-9._:-]{1,128}$",
    "settingsSection": "^[a-z]{1,32}$"
  },
  "argumentTypes": [
    "enum",
    "string",
    "integer",
    "boolean",
    "key"
  ],
  "sideEffects": {
    "agents.tab-attention-cleared": "Selecting Agents clears the Agents tab's attention mark.",
    "centre.view-shown": "A centre view is shown in place of the one that was.",
    "centre.detail-replaced": "The one detail slot of that kind now shows another entity.",
    "centre.detail-closed": "A centre detail closed and the centre went back to Conversation.",
    "queue.item-activated": "Opening a waiting queue item moves it to Active.",
    "queue.read-marked": "Showing a message body can mark it read.",
    "queue.draft-saved": "Leaving a reply editor saves its draft.",
    "settings.opened": "The Settings view opened.",
    "menu.opened": "The menu opened.",
    "list.paged": "A list loaded or showed another page.",
    "list.filtered": "A list's filter changed."
  },
  "pipeline": {
    "source": "orb.source",
    "junctions": [
      {
        "id": "orb.source",
        "region": "left",
        "label": "The main orb"
      },
      {
        "id": "trunk.orb",
        "region": "left",
        "label": "Top pipeline above the orb"
      },
      {
        "id": "rail.left.top",
        "region": "left",
        "label": "Left rail"
      },
      {
        "id": "rail.main.top",
        "region": "main",
        "label": "Main rail"
      },
      {
        "id": "rail.right.top",
        "region": "right",
        "label": "Right rail"
      },
      {
        "id": "rail.overlay.top",
        "region": "overlay",
        "label": "Overlay rail"
      },
      {
        "id": "rail.footer.top",
        "region": "footer",
        "label": "Footer rail"
      }
    ],
    "edges": [
      {
        "id": "edge.orb-rise",
        "from": "orb.source",
        "to": "trunk.orb",
        "region": "left"
      },
      {
        "id": "edge.trunk-left",
        "from": "trunk.orb",
        "to": "rail.left.top",
        "region": "left"
      },
      {
        "id": "edge.trunk-main",
        "from": "trunk.orb",
        "to": "rail.main.top",
        "region": "main"
      },
      {
        "id": "edge.trunk-right",
        "from": "rail.main.top",
        "to": "rail.right.top",
        "region": "right"
      },
      {
        "id": "edge.trunk-overlay",
        "from": "rail.main.top",
        "to": "rail.overlay.top",
        "region": "overlay"
      },
      {
        "id": "edge.trunk-footer",
        "from": "rail.left.top",
        "to": "rail.footer.top",
        "region": "footer"
      }
    ],
    "rails": [
      {
        "id": "rail.left",
        "region": "left",
        "top": "rail.left.top"
      },
      {
        "id": "rail.main",
        "region": "main",
        "top": "rail.main.top"
      },
      {
        "id": "rail.right",
        "region": "right",
        "top": "rail.right.top"
      },
      {
        "id": "rail.overlay",
        "region": "overlay",
        "top": "rail.overlay.top"
      },
      {
        "id": "rail.footer",
        "region": "footer",
        "top": "rail.footer.top"
      }
    ]
  },
  "views": [
    {
      "id": "left.fixed",
      "region": "left",
      "kind": "fixed",
      "label": "Left column"
    },
    {
      "id": "right.tabs",
      "region": "right",
      "kind": "fixed",
      "label": "Right panel tabs"
    },
    {
      "id": "right.folders",
      "region": "right",
      "kind": "tab",
      "label": "Folders",
      "tab": "folders"
    },
    {
      "id": "right.agents",
      "region": "right",
      "kind": "tab",
      "label": "Agents",
      "tab": "agents"
    },
    {
      "id": "right.queue",
      "region": "right",
      "kind": "tab",
      "label": "Queue",
      "tab": "queue"
    },
    {
      "id": "right.workflows",
      "region": "right",
      "kind": "tab",
      "label": "Workflows",
      "tab": "workflows"
    },
    {
      "id": "right.network",
      "region": "right",
      "kind": "tab",
      "label": "Network",
      "tab": "network"
    },
    {
      "id": "right.diagnostics",
      "region": "right",
      "kind": "tab",
      "label": "Diagnostics",
      "tab": "diagnostics"
    },
    {
      "id": "main.tabs",
      "region": "main",
      "kind": "fixed",
      "label": "Centre tabs"
    },
    {
      "id": "main.notice",
      "region": "main",
      "kind": "fixed",
      "label": "Centre notice"
    },
    {
      "id": "main.conversation",
      "region": "main",
      "kind": "tab",
      "label": "Conversation",
      "tab": "conversation",
      "kept": true
    },
    {
      "id": "main.folders",
      "region": "main",
      "kind": "tab",
      "label": "Folders",
      "tab": "folders",
      "kept": true
    },
    {
      "id": "main.main",
      "region": "main",
      "kind": "tab",
      "label": "Main",
      "tab": "main",
      "hidden": true
    },
    {
      "id": "main.workflow",
      "region": "main",
      "kind": "detail",
      "label": "Workflow",
      "tab": "workflow",
      "entity": "workflow"
    },
    {
      "id": "main.thread",
      "region": "main",
      "kind": "detail",
      "label": "Thread",
      "tab": "thread",
      "entity": "session"
    },
    {
      "id": "main.diagnostics",
      "region": "main",
      "kind": "detail",
      "label": "Voice diagnostics",
      "tab": "diagnostics"
    },
    {
      "id": "main.subsystem",
      "region": "main",
      "kind": "detail",
      "label": "Subsystem",
      "tab": "subsystem",
      "entity": "subsystem"
    },
    {
      "id": "main.network",
      "region": "main",
      "kind": "detail",
      "label": "Team server or instance",
      "tab": "network",
      "entity": "node"
    },
    {
      "id": "main.queue",
      "region": "main",
      "kind": "detail",
      "label": "Queue item",
      "tab": "queue",
      "entity": "thread"
    },
    {
      "id": "overlay.controls",
      "region": "overlay",
      "kind": "fixed",
      "label": "Menu button"
    },
    {
      "id": "overlay.menu",
      "region": "overlay",
      "kind": "menu",
      "label": "Menu"
    },
    {
      "id": "overlay.settings",
      "region": "overlay",
      "kind": "modal",
      "label": "Settings"
    },
    {
      "id": "footer.fixed",
      "region": "footer",
      "kind": "fixed",
      "label": "Footer"
    }
  ],
  "commands": [
    {
      "id": "right.tab.select",
      "label": "Select one of the right panel's six tabs",
      "view": "right.tabs",
      "args": {
        "tab": {
          "type": "enum",
          "values": [
            "folders",
            "agents",
            "queue",
            "workflows",
            "network",
            "diagnostics"
          ]
        }
      },
      "required": [
        "tab"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "agents.tab-attention-cleared",
          "when": {
            "tab": "agents"
          }
        }
      ],
      "family": "shell"
    },
    {
      "id": "main.tab.select",
      "label": "Select a centre tab; a detail tab only while its item is open",
      "view": "main.tabs",
      "args": {
        "tab": {
          "type": "enum",
          "values": [
            "conversation",
            "folders",
            "main",
            "workflow",
            "thread",
            "diagnostics",
            "subsystem",
            "network",
            "queue"
          ]
        }
      },
      "required": [
        "tab"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        }
      ],
      "family": "shell"
    },
    {
      "id": "network.open",
      "label": "Open the team server's or one instance's detail in the centre",
      "view": "main.network",
      "args": {
        "kind": {
          "type": "enum",
          "values": [
            "node",
            "server"
          ]
        },
        "nodeId": {
          "type": "key",
          "entity": "node"
        }
      },
      "required": [
        "kind"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        }
      ],
      "family": "network"
    },
    {
      "id": "network.close",
      "label": "Close the team server or instance detail",
      "view": "main.network",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.detail-closed"
        }
      ],
      "family": "network"
    },
    {
      "id": "workflow.open",
      "label": "Open one workflow's detail in the centre",
      "view": "main.workflow",
      "args": {
        "workflowId": {
          "type": "key",
          "entity": "workflow"
        }
      },
      "required": [
        "workflowId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "subagent.open",
      "label": "Open one subagent's detail in the centre, in the workflow slot",
      "view": "main.workflow",
      "args": {
        "sessionId": {
          "type": "key",
          "entity": "session"
        },
        "agentId": {
          "type": "key",
          "entity": "subagent"
        }
      },
      "required": [
        "sessionId",
        "agentId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "workflow.close",
      "label": "Close the workflow or subagent detail",
      "view": "main.workflow",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.detail-closed"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "thread.open",
      "label": "Open one agent's thread in the centre",
      "view": "main.thread",
      "args": {
        "sessionId": {
          "type": "key",
          "entity": "session"
        }
      },
      "required": [
        "sessionId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "thread.close",
      "label": "Close the agent's thread",
      "view": "main.thread",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.detail-closed"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "voice.diagnostics.open",
      "label": "Open voice diagnostics in the centre",
      "view": "main.diagnostics",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "voice.diagnostics.close",
      "label": "Close voice diagnostics",
      "view": "main.diagnostics",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.detail-closed"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "subsystem.open",
      "label": "Open one subsystem's detail in the centre (voice input opens voice diagnostics)",
      "view": "main.subsystem",
      "args": {
        "subsystemId": {
          "type": "key",
          "entity": "subsystem"
        }
      },
      "required": [
        "subsystemId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        },
        {
          "effect": "centre.detail-closed"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "subsystem.close",
      "label": "Close the subsystem's detail",
      "view": "main.subsystem",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.detail-closed"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "queue.open",
      "label": "Open one Queue item in the centre; a waiting one becomes Active",
      "view": "main.queue",
      "args": {
        "threadId": {
          "type": "key",
          "entity": "thread"
        }
      },
      "required": [
        "threadId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        },
        {
          "effect": "queue.item-activated"
        },
        {
          "effect": "queue.read-marked"
        },
        {
          "effect": "queue.draft-saved"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "queue.follow",
      "label": "Follow a link to another Queue item without changing its place",
      "view": "main.queue",
      "args": {
        "threadId": {
          "type": "key",
          "entity": "thread"
        }
      },
      "required": [
        "threadId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        },
        {
          "effect": "queue.read-marked"
        },
        {
          "effect": "queue.draft-saved"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "queue.back",
      "label": "Go back to the Queue item a link was followed from",
      "view": "main.queue",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        },
        {
          "effect": "queue.read-marked"
        },
        {
          "effect": "queue.draft-saved"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "queue.next",
      "label": "Open the next waiting Queue item; it becomes Active",
      "view": "main.queue",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        },
        {
          "effect": "centre.detail-replaced"
        },
        {
          "effect": "queue.item-activated"
        },
        {
          "effect": "queue.read-marked"
        },
        {
          "effect": "queue.draft-saved"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "queue.close",
      "label": "Close the Queue item; its draft is saved",
      "view": "main.queue",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.detail-closed"
        },
        {
          "effect": "queue.draft-saved"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "folders.open",
      "label": "Select one folder and show it in the centre's Folders view",
      "view": "main.folders",
      "args": {
        "folderId": {
          "type": "key",
          "entity": "folder"
        }
      },
      "required": [
        "folderId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "centre.view-shown"
        }
      ],
      "family": "navigation"
    },
    {
      "id": "folders.close",
      "label": "Close the selected folder; the Folders view shows the catalog",
      "view": "main.folders",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [],
      "family": "navigation"
    },
    {
      "id": "agents.filter",
      "label": "Show only the active agents, or all of them, in the Agents list (remembered)",
      "view": "right.agents",
      "args": {
        "show": {
          "type": "enum",
          "values": [
            "active",
            "all"
          ]
        }
      },
      "required": [
        "show"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "list.filtered"
        }
      ],
      "family": "agents"
    },
    {
      "id": "agents.page",
      "label": "Move the Agents list one agent back or on, while more are listed than fit",
      "view": "right.agents",
      "args": {
        "direction": {
          "type": "enum",
          "values": [
            "previous",
            "next"
          ]
        }
      },
      "required": [
        "direction"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "list.paged"
        }
      ],
      "family": "agents"
    },
    {
      "id": "agents.reveal",
      "label": "Move the Agents list until one listed agent's row shows",
      "view": "right.agents",
      "args": {
        "sessionId": {
          "type": "key",
          "entity": "session"
        }
      },
      "required": [
        "sessionId"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "list.paged"
        }
      ],
      "family": "agents"
    },
    {
      "id": "queue.history",
      "label": "Show or hide the Queue's dismissed items; showing reads their first page",
      "view": "right.queue",
      "args": {
        "shown": {
          "type": "boolean"
        }
      },
      "required": [
        "shown"
      ],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "list.paged",
          "when": {
            "shown": true
          }
        }
      ],
      "family": "queue"
    },
    {
      "id": "queue.history.more",
      "label": "Read the next page of the Queue's dismissed items",
      "view": "right.queue",
      "args": {},
      "required": [],
      "actors": [
        "human",
        "brain"
      ],
      "effects": [
        {
          "effect": "list.paged"
        }
      ],
      "family": "queue"
    }
  ],
  "kinds": [
    {
      "kind": "rightTab",
      "entity": null,
      "label": "Right panel tab",
      "parts": [
        "label",
        "attention"
      ],
      "presentations": [
        {
          "view": "right.tabs"
        }
      ],
      "keys": [
        "folders",
        "agents",
        "queue",
        "workflows",
        "network",
        "diagnostics"
      ],
      "family": "shell"
    },
    {
      "kind": "mainTab",
      "entity": null,
      "label": "Centre tab",
      "parts": [
        "label"
      ],
      "presentations": [
        {
          "view": "main.tabs"
        }
      ],
      "keys": [
        "conversation",
        "folders",
        "main",
        "workflow",
        "thread",
        "diagnostics",
        "subsystem",
        "network",
        "queue"
      ],
      "family": "shell"
    },
    {
      "kind": "region",
      "entity": null,
      "label": "A whole region",
      "parts": [],
      "presentations": [
        {
          "view": "left.fixed"
        },
        {
          "view": "right.tabs"
        },
        {
          "view": "main.tabs"
        },
        {
          "view": "footer.fixed"
        }
      ],
      "keys": [
        "left",
        "right",
        "main",
        "overlay",
        "footer"
      ],
      "family": "shell"
    },
    {
      "kind": "node",
      "entity": "node",
      "label": "JARVIS instance",
      "parts": [
        "name",
        "presence",
        "human",
        "cpu",
        "memory",
        "pace",
        "owner",
        "version",
        "lastUpdate",
        "coverage",
        "remaining",
        "accounts"
      ],
      "presentations": [
        {
          "view": "right.network",
          "parts": [
            "name",
            "presence",
            "human",
            "cpu",
            "memory",
            "pace"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "network"
            }
          }
        },
        {
          "view": "main.network",
          "parts": [
            "name",
            "owner",
            "version",
            "presence",
            "lastUpdate",
            "human",
            "cpu",
            "memory",
            "pace",
            "coverage",
            "remaining",
            "accounts"
          ],
          "reveal": {
            "command": "network.open",
            "args": {
              "kind": "node",
              "nodeId": "$key"
            }
          }
        }
      ],
      "family": "network"
    },
    {
      "kind": "teamServer",
      "entity": null,
      "label": "Team server",
      "parts": [
        "readiness",
        "server",
        "database",
        "lastWrite",
        "connection",
        "service"
      ],
      "presentations": [
        {
          "view": "left.fixed",
          "parts": [
            "readiness",
            "server",
            "database",
            "lastWrite"
          ]
        },
        {
          "view": "main.network",
          "parts": [
            "connection",
            "database",
            "service"
          ],
          "reveal": {
            "command": "network.open",
            "args": {
              "kind": "server"
            }
          }
        }
      ],
      "family": "network"
    },
    {
      "kind": "orb",
      "entity": null,
      "label": "The main orb",
      "parts": [
        "canvas",
        "logo"
      ],
      "presentations": [
        {
          "view": "left.fixed"
        }
      ],
      "family": "left"
    },
    {
      "kind": "status",
      "entity": null,
      "label": "Status",
      "parts": [
        "text",
        "stop"
      ],
      "presentations": [
        {
          "view": "left.fixed"
        }
      ],
      "family": "left"
    },
    {
      "kind": "mic",
      "entity": null,
      "label": "Microphone",
      "parts": [
        "meter",
        "health",
        "mute",
        "off"
      ],
      "presentations": [
        {
          "view": "left.fixed"
        }
      ],
      "family": "left"
    },
    {
      "kind": "brainAccount",
      "entity": "provider",
      "label": "Brain account",
      "parts": [
        "name",
        "remaining",
        "pace",
        "reset"
      ],
      "presentations": [
        {
          "view": "left.fixed"
        }
      ],
      "family": "left"
    },
    {
      "kind": "menu",
      "entity": null,
      "label": "Menu",
      "parts": [
        "toggle",
        "settings",
        "updates",
        "restart"
      ],
      "presentations": [
        {
          "view": "overlay.controls",
          "parts": [
            "toggle"
          ]
        },
        {
          "view": "overlay.menu",
          "parts": [
            "settings",
            "updates",
            "restart"
          ]
        }
      ],
      "family": "settings"
    },
    {
      "kind": "settings",
      "entity": null,
      "label": "Settings",
      "parts": [
        "close",
        "nav"
      ],
      "presentations": [
        {
          "view": "overlay.settings"
        }
      ],
      "family": "settings"
    },
    {
      "kind": "settingsSection",
      "entity": "settingsSection",
      "label": "Settings section",
      "parts": [
        "heading",
        "body"
      ],
      "presentations": [
        {
          "view": "overlay.settings"
        }
      ],
      "family": "settings"
    },
    {
      "kind": "teamReminder",
      "entity": null,
      "label": "Sign-in reminder",
      "parts": [
        "text",
        "signIn",
        "later"
      ],
      "presentations": [
        {
          "view": "main.notice"
        }
      ],
      "family": "settings"
    },
    {
      "kind": "footer",
      "entity": null,
      "label": "Footer",
      "parts": [
        "label"
      ],
      "presentations": [
        {
          "view": "footer.fixed"
        }
      ],
      "family": "settings"
    },
    {
      "kind": "agent",
      "entity": "session",
      "label": "Agent",
      "parts": [
        "row",
        "name",
        "state",
        "orb"
      ],
      "presentations": [
        {
          "view": "right.agents",
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "agents"
            }
          }
        }
      ],
      "family": "agents"
    },
    {
      "kind": "agentsList",
      "entity": null,
      "label": "Agents list",
      "parts": [
        "filter",
        "previous",
        "next"
      ],
      "presentations": [
        {
          "view": "right.agents",
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "agents"
            }
          }
        }
      ],
      "family": "agents"
    },
    {
      "kind": "queuePanel",
      "entity": null,
      "label": "Queue",
      "parts": [
        "waiting",
        "active",
        "history",
        "more",
        "new"
      ],
      "presentations": [
        {
          "view": "right.queue",
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "queue"
            }
          }
        }
      ],
      "family": "queue"
    },
    {
      "kind": "queueThread",
      "entity": "thread",
      "label": "Queue item",
      "parts": [
        "row",
        "sender",
        "subject",
        "receipt",
        "turn",
        "header",
        "back",
        "next",
        "close",
        "composer"
      ],
      "presentations": [
        {
          "view": "right.queue",
          "parts": [
            "row",
            "sender",
            "subject",
            "receipt",
            "turn"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "queue"
            }
          }
        },
        {
          "view": "main.queue",
          "parts": [
            "header",
            "back",
            "next",
            "close",
            "composer",
            "turn"
          ],
          "reveal": {
            "command": "queue.open",
            "args": {
              "threadId": "$key"
            }
          }
        }
      ],
      "family": "queue"
    },
    {
      "kind": "queueMessage",
      "entity": "message",
      "label": "Message",
      "parts": [
        "card",
        "body",
        "receipt"
      ],
      "presentations": [
        {
          "view": "main.queue"
        }
      ],
      "family": "queue"
    },
    {
      "kind": "queueAttachment",
      "entity": "attachment",
      "label": "Attachment",
      "parts": [
        "entry"
      ],
      "presentations": [
        {
          "view": "main.queue"
        }
      ],
      "family": "queue"
    },
    {
      "kind": "workflowsList",
      "entity": null,
      "label": "Workflows list",
      "parts": [
        "showDone"
      ],
      "presentations": [
        {
          "view": "right.workflows",
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "workflows"
            }
          }
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflow",
      "entity": "workflow",
      "label": "Workflow",
      "parts": [
        "row",
        "state",
        "tasks",
        "header",
        "close",
        "dismiss",
        "tab.overview",
        "tab.phases",
        "tab.tasks",
        "tab.transcript",
        "tab.progress",
        "tab.waits",
        "tab.decisions",
        "tab.blockers",
        "tab.events"
      ],
      "presentations": [
        {
          "view": "right.workflows",
          "parts": [
            "row",
            "state",
            "tasks",
            "dismiss"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "workflows"
            }
          }
        },
        {
          "view": "main.workflow",
          "parts": [
            "header",
            "close",
            "dismiss",
            "tab.overview",
            "tab.phases",
            "tab.tasks",
            "tab.transcript",
            "tab.progress",
            "tab.waits",
            "tab.decisions",
            "tab.blockers",
            "tab.events"
          ]
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflowPhase",
      "entity": null,
      "keyOf": [
        "workflow",
        "workflowItem"
      ],
      "label": "Workflow phase",
      "parts": [
        "row",
        "state"
      ],
      "presentations": [
        {
          "view": "main.workflow"
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflowTask",
      "entity": null,
      "keyOf": [
        "workflow",
        "workflowItem"
      ],
      "label": "Workflow task",
      "parts": [
        "row",
        "state",
        "progress"
      ],
      "presentations": [
        {
          "view": "main.workflow"
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflowDecision",
      "entity": null,
      "keyOf": [
        "workflow",
        "workflowItem"
      ],
      "label": "Workflow decision",
      "parts": [
        "row",
        "options",
        "answer"
      ],
      "presentations": [
        {
          "view": "main.workflow"
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflowWait",
      "entity": null,
      "keyOf": [
        "workflow",
        "workflowItem"
      ],
      "label": "Workflow wait",
      "parts": [
        "row"
      ],
      "presentations": [
        {
          "view": "main.workflow"
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflowBlocker",
      "entity": null,
      "keyOf": [
        "workflow",
        "workflowItem"
      ],
      "label": "Workflow blocker",
      "parts": [
        "row"
      ],
      "presentations": [
        {
          "view": "main.workflow"
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflowTranscript",
      "entity": "workflow",
      "label": "Workflow transcript",
      "parts": [
        "list",
        "earlier"
      ],
      "presentations": [
        {
          "view": "main.workflow"
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "workflowEvents",
      "entity": "workflow",
      "label": "Workflow events",
      "parts": [
        "list",
        "filter"
      ],
      "presentations": [
        {
          "view": "main.workflow"
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "subagent",
      "entity": "subagent",
      "label": "Subagent",
      "parts": [
        "row",
        "state",
        "header",
        "close",
        "transcript"
      ],
      "presentations": [
        {
          "view": "right.workflows",
          "parts": [
            "row",
            "state"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "workflows"
            }
          }
        },
        {
          "view": "main.workflow",
          "parts": [
            "header",
            "close",
            "transcript"
          ]
        }
      ],
      "family": "workflows"
    },
    {
      "kind": "subsystem",
      "entity": "subsystem",
      "label": "Subsystem",
      "parts": [
        "row",
        "status",
        "header",
        "close",
        "checks",
        "messages"
      ],
      "presentations": [
        {
          "view": "right.diagnostics",
          "parts": [
            "row",
            "status"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "diagnostics"
            }
          }
        },
        {
          "view": "main.subsystem",
          "parts": [
            "header",
            "close",
            "checks",
            "messages"
          ],
          "reveal": {
            "command": "subsystem.open",
            "args": {
              "subsystemId": "$key"
            }
          }
        }
      ],
      "family": "diagnostics"
    },
    {
      "kind": "subsystemCheck",
      "entity": null,
      "keyOf": [
        "subsystem",
        "check"
      ],
      "label": "Subsystem check",
      "parts": [
        "row"
      ],
      "presentations": [
        {
          "view": "main.subsystem"
        }
      ],
      "family": "diagnostics"
    },
    {
      "kind": "voice",
      "entity": null,
      "label": "Voice input health",
      "parts": [
        "summary",
        "open",
        "range",
        "zoom",
        "overview",
        "timeline",
        "incidents",
        "notifications",
        "messages",
        "close"
      ],
      "presentations": [
        {
          "view": "right.diagnostics",
          "parts": [
            "summary",
            "open"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "diagnostics"
            }
          }
        },
        {
          "view": "main.diagnostics",
          "parts": [
            "range",
            "zoom",
            "overview",
            "timeline",
            "incidents",
            "notifications",
            "messages",
            "close"
          ],
          "reveal": {
            "command": "voice.diagnostics.open",
            "args": {}
          }
        }
      ],
      "family": "diagnostics"
    },
    {
      "kind": "voiceIncident",
      "entity": "incident",
      "label": "Voice incident",
      "parts": [
        "row"
      ],
      "presentations": [
        {
          "view": "main.diagnostics"
        }
      ],
      "family": "diagnostics"
    },
    {
      "kind": "foldersList",
      "entity": null,
      "label": "Folder catalog",
      "parts": [
        "search",
        "open",
        "role",
        "availability",
        "registered",
        "discovered",
        "ignored",
        "removed",
        "add"
      ],
      "presentations": [
        {
          "view": "right.folders",
          "parts": [
            "search",
            "open"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "folders"
            }
          }
        },
        {
          "view": "main.folders",
          "parts": [
            "search",
            "role",
            "availability",
            "registered",
            "discovered",
            "ignored",
            "removed",
            "add"
          ]
        }
      ],
      "family": "folders"
    },
    {
      "kind": "folder",
      "entity": "folder",
      "label": "Folder",
      "parts": [
        "row",
        "name",
        "availability",
        "detail",
        "location",
        "contents",
        "close"
      ],
      "presentations": [
        {
          "view": "right.folders",
          "parts": [
            "row",
            "name",
            "availability"
          ],
          "reveal": {
            "command": "right.tab.select",
            "args": {
              "tab": "folders"
            }
          }
        },
        {
          "view": "main.folders",
          "parts": [
            "row",
            "detail",
            "location",
            "contents",
            "close"
          ]
        }
      ],
      "family": "folders"
    },
    {
      "kind": "folderEntry",
      "entity": null,
      "keyOf": [
        "folder",
        "folderEntry"
      ],
      "label": "Folder entry",
      "parts": [
        "row",
        "crumb",
        "preview"
      ],
      "presentations": [
        {
          "view": "main.folders"
        }
      ],
      "family": "folders"
    },
    {
      "kind": "folderCandidate",
      "entity": "candidate",
      "label": "Discovered folder",
      "parts": [
        "row"
      ],
      "presentations": [
        {
          "view": "main.folders"
        }
      ],
      "family": "folders"
    },
    {
      "kind": "conversation",
      "entity": null,
      "label": "Conversation",
      "parts": [
        "history",
        "earlier",
        "input"
      ],
      "presentations": [
        {
          "view": "main.conversation"
        }
      ],
      "family": "conversation"
    },
    {
      "kind": "conversationRecord",
      "entity": "record",
      "label": "Conversation record",
      "parts": [
        "record",
        "user",
        "answer",
        "agent",
        "expand"
      ],
      "presentations": [
        {
          "view": "main.conversation"
        }
      ],
      "family": "conversation"
    },
    {
      "kind": "capture",
      "entity": "capture",
      "label": "Capture",
      "parts": [
        "box",
        "body",
        "recipient",
        "actions"
      ],
      "presentations": [
        {
          "view": "main.conversation"
        },
        {
          "view": "main.main"
        }
      ],
      "family": "conversation"
    },
    {
      "kind": "sources",
      "entity": "record",
      "label": "Sources",
      "parts": [
        "chip",
        "items"
      ],
      "presentations": [
        {
          "view": "main.conversation"
        }
      ],
      "family": "conversation"
    },
    {
      "kind": "agentThread",
      "entity": "session",
      "label": "Agent thread",
      "parts": [
        "header",
        "close",
        "earlier"
      ],
      "presentations": [
        {
          "view": "main.thread"
        }
      ],
      "family": "conversation"
    },
    {
      "kind": "threadRecord",
      "entity": null,
      "keyOf": [
        "session",
        "record"
      ],
      "label": "Thread record",
      "parts": [
        "record"
      ],
      "presentations": [
        {
          "view": "main.thread"
        }
      ],
      "family": "conversation"
    }
  ],
  "families": [
    {
      "family": "shell",
      "owner": "integrator"
    },
    {
      "family": "network",
      "owner": "integrator"
    },
    {
      "family": "navigation",
      "owner": "navigation"
    },
    {
      "family": "left",
      "owner": "coverage-left-settings"
    },
    {
      "family": "settings",
      "owner": "coverage-left-settings"
    },
    {
      "family": "agents",
      "owner": "coverage-right"
    },
    {
      "family": "queue",
      "owner": "coverage-right"
    },
    {
      "family": "workflows",
      "owner": "coverage-main"
    },
    {
      "family": "diagnostics",
      "owner": "coverage-right"
    },
    {
      "family": "folders",
      "owner": "coverage-main"
    },
    {
      "family": "conversation",
      "owner": "coverage-main"
    }
  ]
} as const;

type C = typeof UI_CONTRACT;
export type Region = C["regions"][number];
export type Actor = C["actors"][number];
export type Status = C["statuses"][number];
export type Lifecycle = C["lifecycle"][number];
export type Arrival = C["arrivals"][number];
export type EndpointStyle = C["endpointStyles"][number];
export type RevealMode = C["revealModes"][number];
export type Visibility = C["visibility"][number];
export type ViewKind = C["viewKinds"][number];
export type AvailabilityReason = keyof C["availabilityReasons"];
export type SideEffect = keyof C["sideEffects"];
export type EntityKind = keyof C["entityKeys"];
export type ViewId = C["views"][number]["id"];
export type CommandId = C["commands"][number]["id"];
export type TargetKind = C["kinds"][number]["kind"];
export type PartOf<K extends TargetKind> = Extract<C["kinds"][number], { kind: K }>["parts"][number];
export type JunctionId = C["pipeline"]["junctions"][number]["id"];
export type RailId = C["pipeline"]["rails"][number]["id"];
export type EdgeId = C["pipeline"]["edges"][number]["id"];

/** One presentation of one target: which entity, in which view, which part. */
export interface TargetRef {
  region: Region;
  view: ViewId;
  kind: TargetKind;
  key?: string;
  part?: string;
}

export interface Availability { available: boolean; reason?: AvailabilityReason }

export type Operation =
  | { kind: "map"; region?: Region; view?: ViewId; cursor?: string }
  | { kind: "navigate"; commandId: CommandId; args: Record<string, unknown> }
  | { kind: "indicate"; target: TargetRef; reveal?: RevealMode; style?: EndpointStyle;
      holdMs?: number; label?: string }
  | { kind: "clear"; cueId?: string }
  | { kind: "journey"; steps: (Extract<Operation, { kind: "navigate" }>
                              | Extract<Operation, { kind: "indicate" }>)[] };

/** `ui_request`, server to the one bound page. */
export interface UIRequest {
  type: "ui_request";
  protocol: number;
  requestId: string;
  clientId: string;
  epoch: number;
  /** The turn the request belongs to (the brain's caller binding). */
  turn: string | null;
  expectedNavigationRevision: number | null;
  /** Epoch milliseconds; a request seen after it is refused as expired. */
  expiresAt: number;
  operation: Operation;
}

/** `ui_result`, the page's one answer to one request. */
export interface UIResult {
  type: "ui_result";
  requestId: string;
  clientId: string;
  epoch: number;
  status: Status;
  navigationRevision: number;
  layoutRevision: number;
  effects: SideEffect[];
  cueId?: string;
  arrival?: Arrival;
  resolvedTarget?: TargetRef;
  reason?: string;
  /** The map operation's bounded answer. */
  map?: unknown;
  /** A journey's per-step outcomes, in order. */
  steps?: Omit<UIResult, "type" | "requestId" | "clientId" | "epoch" | "steps">[];
}

/** `ui_event`, a lifecycle step of a request under way. */
export interface UIEvent {
  type: "ui_event";
  requestId: string;
  clientId: string;
  epoch: number;
  event: Lifecycle;
  at: number;
  cueId?: string;
}

/** `ui_hello`, the page announcing itself on every (re)connect. */
export interface UIHello {
  type: "ui_hello";
  protocol: number;
  clientId: string;
  capabilities: string[];
  navigationRevision: number;
  layoutRevision: number;
  visible: boolean;
  reducedMotion: boolean;
}

/** `ui_welcome`, the server's answer: this connection's epoch. */
export interface UIWelcome { type: "ui_welcome"; clientId: string; epoch: number }

export interface CommandArgs {
  "agents.filter": { show: "active" | "all"; };
  "agents.page": { direction: "previous" | "next"; };
  "agents.reveal": { sessionId: string; };
  "folders.close": { };
  "folders.open": { folderId: string; };
  "main.tab.select": { tab: "conversation" | "folders" | "main" | "workflow" | "thread" | "diagnostics" | "subsystem" | "network" | "queue"; };
  "network.close": { };
  "network.open": { kind: "node" | "server"; nodeId?: string; };
  "queue.back": { };
  "queue.close": { };
  "queue.follow": { threadId: string; };
  "queue.history": { shown: boolean; };
  "queue.history.more": { };
  "queue.next": { };
  "queue.open": { threadId: string; };
  "right.tab.select": { tab: "folders" | "agents" | "queue" | "workflows" | "network" | "diagnostics"; };
  "subagent.open": { sessionId: string; agentId: string; };
  "subsystem.close": { };
  "subsystem.open": { subsystemId: string; };
  "thread.close": { };
  "thread.open": { sessionId: string; };
  "voice.diagnostics.close": { };
  "voice.diagnostics.open": { };
  "workflow.close": { };
  "workflow.open": { workflowId: string; };
}
