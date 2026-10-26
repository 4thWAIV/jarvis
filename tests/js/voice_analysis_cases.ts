// Runs frontend/src/voiceAnalysis.ts on small records and prints the results
// as JSON for tests/test_voice_analysis.py. Bundled with esbuild.
import { beforeDrops, groupMessages, noticesBySource, quietComparison } from "../../frontend/src/voiceAnalysis";

const sample = (at: number, extra: Record<string, unknown> = {}) => ({
  at, capture: "c", receivedSec: 1, decodedSec: 1, backlogSec: 0, oldestSec: 0, rtf: 0.1,
  loopDelayMs: 1, processCpu: 50, loadAvg: 2, framesCaptured: 10, framesSent: 10, heldSec: 0,
  socketSec: 0, longTaskMs: 0, longTasks: 0, tabHidden: false, notices: 1,
  noticesBySource: { Input: 1 }, voiceMsgs: 1, sessionMsgs: 0, renders: 2, finals: 0, typed: 0,
  quiet: false, state: "healthy", cause: "", ...extra,
}) as any;
const event = (at: number, kind: string, extra: Record<string, unknown> = {}) =>
  ({ id: at, at, kind, capture: "c", source: null, text: null, cause: null, topProcesses: [], ...extra }) as any;

const samples = [
  ...Array.from({ length: 100 }, (_, i) => sample(1000 + i)),
  ...Array.from({ length: 30 }, (_, i) => sample(1100 + i, { notices: 9, longTaskMs: 300, renders: 20,
                                                            noticesBySource: { Input: 4, Status: 5 } })),
  ...Array.from({ length: 3600 }, (_, i) => sample(2000 + i, { quiet: true })),
];
const events = [
  event(1130, "overload"),
  event(1131, "message", { source: "Input", text: "fell behind" }),
  event(1150, "message", { source: "Input", text: "fell behind" }),
  event(1300, "message", { source: "Input", text: "fell behind" }),
  event(1140, "message", { source: "Status", text: "restarting" }),
];
console.log(JSON.stringify({
  groups: groupMessages(events, [{ source: "Status", text: "only here", at: 1500 * 1000 },
                                 { source: "Status", text: "restarting", at: 1140.5 * 1000 }]),
  sources: noticesBySource(samples.slice(0, 130)),
  before: beforeDrops(samples.slice(0, 130), events),
  quiet: quietComparison(samples, [...events, event(3000, "overload"), event(3001, "overload")]),
}));
