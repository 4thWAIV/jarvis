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
  "tabs.right.workflows": "Work",
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
  "tabs.main.knowledge": "Knowledge",
  "tabs.main.network": "Instance",
  "tabs.main.teamServer": "Team server",
  "tabs.main.queue": "Message",
  "tabs.main.package": "Work package",
  "tabs.main.file": "File",
  "tabs.main.settings": "Settings",
  "tabs.main.work": "Work item",
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
  "language.downloading": "Downloading, {done} of {total}",
  "language.verifying": "Checking the download",
  "language.installing": "Installing",
  "language.download": "Download ({size})",
  "language.unreadable": "The language could not be read ({error}); trying again.",
  "language.reading": "Reading the language…",

  // What the brain's map (ui_map) calls the targets that have no name of
  // their own; a target named by its record (a folder, an agent) shows
  // that name. In the language in force, like everything shown.
  "targets.agentsList": "Agents list",
  "targets.registeredFolders": "Registered folders",
  "targets.footer": "Footer",
  "targets.menuButton": "Menu button",
  "targets.microphone": "Microphone",
  "targets.orb": "The orb",
  "targets.status": "Status",
  "targets.takeoverBar": "Takeover bar",
  "targets.teamServer": "Team server",
  "targets.signInReminder": "Sign-in reminder",
  "targets.updatesNotice": "Updates waiting",
  "targets.voiceHealth": "Voice health",
  "targets.account": "{provider} account",
  "targets.settingsSection": "Settings, {section}",

  // Words followed by what another program said, as it came (locale.ts
  // SaidText and saidShown; voice input's notices).
  "said.withDetail": "{message} Detail: {detail}",

  // What JARVIS says about the team server, by team_client's code
  // (TEAM_SAID, whose English is this, word for word; Settings, Team, the
  // team server view and the Diagnostics row show it).
  "team.error.sessionUnusable": "The team server sent an unusable session.",
  "team.error.keychainSaveFailed": "JARVIS couldn't save the team session in your Keychain.",
  "team.error.addressMissing": "Enter the team server's address, like https://team.example.com.",
  "team.error.addressHasPath": "Enter just the server's address, without a path.",
  "team.error.addressNotHttps": "The team server's address must start with https://.",
  "team.error.addressNotFound": "JARVIS couldn't find that server — check the address.",
  "team.error.addressPrivate": "That address is on a private network; JARVIS only connects to public team servers.",
  "team.error.noAnswer": "The team server didn't answer ({kind}).",
  "team.error.redirected": "The team server tried to redirect JARVIS elsewhere; JARVIS doesn't follow team-server redirects.",
  "team.error.notTeamServer": "That address isn't a JARVIS team server.",
  "team.error.versionMismatch": "That team server speaks a different version of the protocol; update JARVIS or the server.",
  "team.error.notReady": "The team server isn't ready yet; try again in a minute.",
  "team.error.identityChangedSinceLast": "This server's identity changed since JARVIS last connected. Disconnect first if that's expected.",
  "team.error.pairingRefused": "The team server refused to start pairing ({status}).",
  "team.error.approvalElsewhere": "The team server gave an approval link on another site.",
  "team.error.pairingExpired": "The pairing code expired. Start again.",
  "team.error.pairingDenied": "The pairing was denied on the team server.",
  "team.error.pairingFailed": "Pairing failed ({status}).",
  "team.error.pairingUnexpected": "Pairing failed unexpectedly. Start again.",
  "team.error.identityChangedPairing": "The team server's identity changed during pairing.",
  "team.error.noSessionSignIn": "JARVIS has no saved session for this server; sign in again.",
  "team.error.noSession": "JARVIS has no saved session for this server.",
  "team.error.sessionEnded": "The team server ended this session; sign in again.",
  "team.error.answered": "The team server answered {status}.",
  "team.error.statusUnreadable": "The team server sent an unreadable status.",
  "team.error.identityChanged": "The team server's identity changed; JARVIS stopped using it. Disconnect and connect again if that's expected.",
  "team.error.databaseUnreachable": "The team server can't reach its database.",
  "team.error.nodeUpdateFailed": "JARVIS couldn't put together its node update.",
  "team.error.snapshotUnreadable": "The team server sent an unreadable network snapshot.",
  "team.error.notConnected": "JARVIS isn't connected to a team server.",

  // Why a second of voice input was not healthy, by voice_diagnostics'
  // code (CAUSES, whose English is this, word for word; the Diagnostics
  // view, the health dot and the voice input row show it).
  "voice.cause.noFrames": "No audio from the microphone",
  "voice.cause.tabHidden": "The JARVIS tab is hidden, so Chrome slows it",
  "voice.cause.audioHeld": "{seconds} s of audio waiting in the browser",
  "voice.cause.pageBusy": "The page was busy for {ms} ms",
  "voice.cause.audioInConnection": "{seconds} s of audio waiting in the connection",
  "voice.cause.serverLate": "JARVIS's server was {ms} ms late",
  "voice.cause.machineBusy": "Machine busy (load {load})",
  "voice.cause.jarvisBusy": "JARVIS busy (CPU {cpu}%)",

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
