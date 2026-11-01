/**
 * The shell's words in English: the tabs, the menu, the language control,
 * and the sizes and ages every region shows (messages/en.ts explains how a
 * message is written). Its Spanish is shell.es.ts. The integrator's.
 */

export const shellEn = {
  // The right panel's tabs (RightPanel.tsx), by their registered tab id.
  "tabs.right.folders": "Folders",
  "tabs.right.agents": "Agents",
  "tabs.right.queue": "Queue",
  "tabs.right.workflows": "Workflows",
  "tabs.right.network": "Network",
  "tabs.right.diagnostics": "Diagnostics",

  // The centre area's tabs (CentreArea.tsx), by their registered tab id. A
  // detail's tab shows its item's own name when it has one.
  "tabs.main.conversation": "Conversation",
  "tabs.main.folders": "Folders",
  "tabs.main.main": "Main",
  "tabs.main.thread": "Thread",
  "tabs.main.diagnostics": "Diagnostics",
  "tabs.main.subsystem": "Subsystem",
  "tabs.main.network": "Instance",
  "tabs.main.teamServer": "Team server",
  "tabs.main.queue": "Message",
  "tabs.main.workflow": "Workflow",
  "tabs.main.subagent": "Subagent",

  // The ⋮ menu (MenuControl.tsx).
  "menu.button": "Menu",
  "menu.settings": "Settings",
  "menu.updates": "Check for updates",
  "menu.restart": "Restart Server",
  "menu.restarting": "Restarting the server.",
  "menu.restartFailed": "The restart failed: the server did not answer.",

  // Settings → Voice, the language (LanguageControl.tsx).
  "language.label": "Language",
  "language.loading": "Loading the speech model; this takes a few seconds.",
  "language.spanishModel": "Spanish speech model",
  "language.installed": "Installed",
  "language.downloading": "Downloading, {done} of {total}",
  "language.verifying": "Checking the download",
  "language.installing": "Installing",
  "language.notInstalled": "Not installed",
  "language.download": "Download ({size})",
  "language.unreadable": "The language could not be read ({error}); trying again.",
  "language.reading": "Reading the language…",

  // An agent's subagents at work (agents.ts workingCount, on the Agents tab).
  "agents.working": "{count} working",
  "agents.workingCapped": "{count}+ working",

  // Sizes and ages (LanguageControl.tsx, timeFormat.ts).
  "units.megabytes": "{amount} MB",
  "time.justNow": "just now",
  "time.ago": "{age} ago",
  "time.short.seconds": "{n}s",
  "time.short.minutes": "{n}m",
  "time.short.hours": "{n}h",
  "time.short.days": "{n}d",
  "time.short.weeks": "{n}w",
  "time.short.years": "{n}y",
  "time.seconds": { one: "{count} second", other: "{count} seconds" },
  "time.minutes": { one: "{count} minute", other: "{count} minutes" },
  "time.hours": { one: "{count} hour", other: "{count} hours" },
  "time.days": { one: "{count} day", other: "{count} days" },
} as const;
