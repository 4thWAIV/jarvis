# One channel, round 2: the choices made

- The Inbox keeps the tab id `queue` (only its label changed: "Inbox", Spanish "Recibidos"), so every `right.tab.select {tab: "queue"}`, revealer and test selector by id keeps working; the contract's `queuePanel` kind is labelled Inbox.
- `work.tab.select` / `work.open` take `messages` in place of `decisions` (enum `messages, overview, blocking, pieces, reports`); an item opens on Messages by default.
- Messages are not marked read by being shown: the brain's `work.open` must never mark anything, so it is his Mark read (each message) or Mark all read (the tab), or Read heard in full.
- A Work decision asked the old way (`work_report.py decision`, until round 3) is answered where its message is, with the same DecisionCard and `answerWorkDecision` as before; one asked before it had a message is shown above the conversation, so no open decision is ever unanswerable on the page.
- Package questions are no longer answered on the Work item: Open shows the package, where he votes; what he chose is shown under the message ("You chose: ..."), read from the item's `packages`.
- The message JSON gains `source` ({kind, id}) so the Messages tab finds a Work decision by its key, never by its words.
- `GET /api/messages/general`'s `new` is now every new message with no item (`conversation_log.new_general`), not those on the page read, so the counts are right.
- The live hint is a new `/ws/sessions` type `inbox` (`server.INBOX_HINT`), sent from `_hint_conversation` for every message recorded or moved and anything on a Work item's thread; `messages` was already the people Queue's hint.
- "N new messages" in Updates counts agents' new messages (Work items and none) plus people's waiting unread threads (`unread_reminder`), the same things the Inbox lists; attention items that are messages are no longer listed as updates but stay on record (digests, Mark all seen).
- On a reading view the updates chip counts facts plus new messages and its dropdown shows the messages line first.
- The Inbox lists the general messages that are not done (new, read and answered), newest first, clamped to four lines each; Work items appear only while they have new messages.
- One card for every message, `MessageCard.tsx`, used by both the Messages tab and the Inbox; `PackageMark` moved there from the deleted `QueuePackages.tsx`.
- The Packages section and its Dismiss and Closed list are gone from the page (the brain's `close_inbox_package` and the routes stay); the now-unused `inbox` / `closedInbox` stores, `closeInbox`, `inboxProgress`, `optionFor` and their catalog words were removed.
- The store file is `messagesToHim.ts`, not `inboxMessages.ts`: macOS's case-insensitive file system resolved `./InboxMessages` (the component) to it.
- The need words say "on the Messages tab" (work_api, catalogs and make_package's `--work` help).
- The UI contract: `workItem` parts `messages`, `messageCount`, `tab.messages` (dropped `decisions`, `packages`, `answered`, `tab.decisions`), the `workPackageQuestion` and `inboxPackage` kinds and the `inboxPackage` entity removed, `inboxWork` and `inboxMessage` kinds and the `toHim` entity added, `updatesNotice` parts `messages` and `openInbox`.
