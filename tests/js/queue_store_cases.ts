// Runs frontend/src/queueStore.ts on Tim's queue as JARVIS serves it (built
// by tests/messaging_queue_scenario.py and passed as a JSON file) and prints
// what the page would show, as JSON, for tests/test_messaging_queue_store.py.
// Bundled with esbuild.
import { readFileSync } from "node:fs";
import * as Q from "../../frontend/src/queueStore";

const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
const threads: Record<string, string> = input.threads;
const messages: Record<string, string> = input.messages;
// Each item by its first message's name.
const name = Object.fromEntries(Object.entries(threads).filter(([n, t]) => messages[n] === t).map(([n, t]) => [t, n]));
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));
const names = (items: Q.ItemRecord[]) => items.map((i) => name[i.work_state.thread_id] ?? i.work_state.thread_id);

const out: Record<string, unknown> = {};
const s = Q.applySnapshot(Q.EMPTY, input.snapshot);
out.waiting = names(Q.waiting(s));
out.active = names(Q.active(s));
out.unsent = Q.unsentDrafts(s).map((d) => d.message_id);
out.attention = Q.attention(s);

// The same snapshot again, and an item read again on its own, change nothing.
const again = Q.applyItem(Q.applySnapshot(s, clone(input.snapshot)), clone(input.snapshot.items[0]), true);
out.repeatChangesNothing = JSON.stringify(again) === JSON.stringify(s);

const dir: Q.Directory = input.directory;
out.cues = Object.fromEntries(Object.entries(threads).filter(([n, t]) => name[t] === n && s.items[t]).map(([n, t]) => {
  const c = Q.cues(s, t)!;
  return [n, { ...c, sending: c.sending?.send.state ?? null, turnWords: Q.turnWords(dir, s.items[t].work_state),
               messages: Q.messagesOf(s, t).length }];
}));

// Put back keeps the original arrival: the item goes back where it was.
const ana = threads["ana-numbers"];
const putBack = Q.withPending(s, { [ana]: { command: "put_back", expected_revision: s.items[ana].work_state.revision } });
out.putBackWaiting = names(Q.waiting(putBack));
// A refusal takes the pending command away: the item is as it was.
out.putBackUndone = names(Q.active(Q.withPending(s, {})));

// An older snapshot arriving late moves nothing back.
const old = clone(input.snapshot);
for (const item of old.items) {
  if (item.work_state.thread_id === ana) {
    item.work_state.revision -= 2;
    item.work_state.placement = "queued";
  }
  for (const m of item.messages) {
    if (m.message.message_id === messages["release-reply"]) {
      m.receipts.revision = 1;
      m.receipts.received_at = null;
      m.receipts.nodes = [];
    }
  }
}
const afterOld = Q.applySnapshot(s, old);
out.oldSnapshot = {
  anaPlacement: afterOld.items[ana].work_state.placement,
  releaseReplyDelivery: Q.delivery(afterOld.messages[messages["release-reply"]].message,
                                   afterOld.receipts[messages["release-reply"]]),
};

// A read that arrives before its received: read is read, received stays unset.
const early = clone(s.receipts[messages["deploy"]]);
early.read_at = "2026-10-29T15:00:00Z";
early.received_at = null;
early.revision += 5;
const readFirst = Q.applyMessage(s, { ...s.messages[messages["deploy"]], receipts: early });
out.readBeforeReceived = {
  delivery: Q.delivery(readFirst.messages[messages["deploy"]].message, readFirst.receipts[messages["deploy"]]),
  received: readFirst.receipts[messages["deploy"]].received_at,
  turn: Q.turn(readFirst.items[threads["deploy"]].work_state),
};

// Replied comes from an accepted direct reply; a related link is not one.
out.repliesToRelease = Q.repliesTo(s, messages["release"]).map((m) => m.message_id);
out.repliesToDeploy = Q.repliesTo(s, messages["deploy"]).map((m) => m.message_id);

// An item a newer snapshot no longer lists leaves Waiting, and is kept.
const without = clone(input.snapshot);
without.items = without.items.filter((i: { work_state: { thread_id: string } }) => i.work_state.thread_id !== threads["creds"]);
const dropped = Q.applySnapshot(s, without);
out.droppedListed = names(Q.waiting(dropped)).includes("creds");
out.droppedKept = !!dropped.items[threads["creds"]];

// A snapshot for another pairing is not this owner's.
const other = clone(input.snapshot);
other.owner_github_id = 4242;
other.items = [];
other.unsent = [];
out.otherPairing = Object.keys(Q.applySnapshot(s, other).items).length;

// Dismissed items, a page at a time; Restore brings one back to Waiting.
const withHistory = Q.applyHistory(s, input.history, true);
out.dismissed = names(Q.dismissed(withHistory));
const lunch = threads["lunch"];
const restoring = Q.withPending(withHistory, { [lunch]: { command: "restore", expected_revision: withHistory.items[lunch].work_state.revision } });
out.restoringWaiting = names(Q.waiting(restoring)).includes("lunch");
out.restoringDismissed = names(Q.dismissed(restoring));
out.lunchCues = (({ readByYou, unread, dismissed }) => ({ readByYou, unread, dismissed }))(Q.cues(withHistory, lunch)!);

// Opening a waiting item shows it in Active at once.
const newest = threads["newest"];
const opening = Q.withPending(s, { [newest]: { command: "open", expected_revision: s.items[newest].work_state.revision } });
out.openingActive = names(Q.active(opening)).includes("newest");

// Whose turn on a self item stays the owner's whatever the cue asks.
const self = threads["note-self"];
out.selfTurnAfterCorrection = Q.turn(Q.predicted(s.items[self].work_state,
  { command: "set_action_owner", action_owner_github_id: 4242, expected_revision: 1 }));

// A message to oneself: the owner's read is their own cue, never "Read by"
// on the delivery line.
const selfRead = clone(s.receipts[messages["note-self"]]);
selfRead.read_at = "2026-10-29T15:00:00Z";
selfRead.revision += 1;
const afterSelfRead = Q.applyMessage(s, { ...s.messages[messages["note-self"]], receipts: selfRead });
out.selfReadDelivery = Q.cues(afterSelfRead, threads["note-self"])!.delivery;

// Who and which JARVIS, with the safe fallbacks.
out.labels = {
  me: Q.recipientLabel(dir, input.directory.owner),
  matt: Q.recipientLabel(dir, input.ids.MATT),
  ana: Q.recipientLabel(dir, input.ids.ANA),
  former: Q.personName(dir, input.ids.FORMER),
  gone: Q.jarvisLabel(dir, input.ids.GONE_NODE),
  maxHandle: Q.handle(dir, input.ids.MAX),
};
out.sizes = [0, 1, 812, 48213, 3_355_443].map(Q.sizeText);

console.log(JSON.stringify(out));
