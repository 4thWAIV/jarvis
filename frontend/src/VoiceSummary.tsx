/**
 * Voice health at the top of the Diagnostics tab, in pictures at the panel's
 * fixed width: the health orb with the state and its cause in one line; the
 * five-stage pipeline track; the last two minutes of how far recognition is
 * behind, against the 1 s warning and the 2 s line where speech is dropped,
 * with each drop marked; the recogniser's speed as a ring; the server's and
 * the machine's load and the page's notifications as chips; and the last
 * hour's drops, reconnects and errors. While the page is listening it moves
 * with the health the server sends each second; otherwise it shows the
 * record. Clicking it opens the Diagnostics view in the centre area.
 */

import { useSyncExternalStore } from "react";
import { useTick } from "./WorkflowsView";
import { Ring } from "./WorkflowVisuals";
import { Chip, PipelineTrack, Sparkline, tone, VoiceOrb } from "./VoiceVisuals";
import { BACKLOG_DROP_SEC, BACKLOG_WARN_SEC, health, HEALTH_WORDS, openDiagnostics, recentHealth,
         summary } from "./voiceHealth";

const TWO_MINUTES = 120;

export function VoiceSummary() {
  useTick(1000);
  const live = useSyncExternalStore(health.subscribe, health.get);
  const recent = useSyncExternalStore(recentHealth.subscribe, recentHealth.get);
  const record = useSyncExternalStore(summary.subscribe, summary.get);
  const s = record.value;
  const state = live?.state ?? s?.state ?? "idle";
  const cause = live ? live.cause : s?.cause ?? "";
  const pipeline = live?.pipeline ?? s?.pipeline;
  const last = s?.last2min[s.last2min.length - 1];

  // The sparkline: this page's own seconds while it listens, else the record's.
  const nowMs = performance.now();
  const points = recent.length
    ? recent.map((h) => ({ t: (h.at - nowMs) / 1000, v: h.backlogSec }))
    : (s?.last2min ?? []).map((x) => ({ t: x.at - s!.now, v: x.backlogSec }));
  const rtf = live ? live.rtf : last?.rtf ?? null;
  const loopMs = live?.loopDelayMs ?? last?.loopDelayMs ?? null;
  const cpu = live?.processCpu ?? last?.processCpu ?? null;
  const load = live?.loadAvg ?? last?.loadAvg ?? null;
  const notices = last?.notices ?? null;
  const hour = s?.incidentsLastHour;

  return (
    <section className="voice-summary" aria-label="Voice health">
      <button type="button" className="voice-summary-open" onClick={() => openDiagnostics.set(true)}
              title="Open the Diagnostics view">
        <span className="voice-health-row">
          <VoiceOrb state={state} size={44} />
          <span className="voice-health-words">
            <b className="voice-health">{HEALTH_WORDS[state]}</b>
            <span className="voice-cause">{cause || (state === "idle" ? "The microphone is not streaming" : " ")}</span>
          </span>
        </span>
        {pipeline && <PipelineTrack pipeline={pipeline} />}
        <Sparkline label="How far recognition is behind, last two minutes" points={points}
                   from={-TWO_MINUTES} to={0} max={BACKLOG_DROP_SEC * 1.25}
                   lines={[{ at: BACKLOG_DROP_SEC, label: "2 s drop", tone: "bad" },
                           { at: BACKLOG_WARN_SEC, label: "1 s", tone: "warn" }]} />
        <span className="voice-readings">
          <Ring done={Math.round(Math.min(rtf ?? 0, 1) * 100)} total={100} size={40}
                label="Recogniser time per second of speech"
                text={rtf == null ? "—" : rtf.toFixed(2)} />
          <span className="voice-chips">
            <Chip tone={tone(loopMs, 100, 200)} title="How late the server runs a timer">
              loop {loopMs == null ? "—" : `${Math.round(loopMs)}ms`}</Chip>
            <Chip tone={tone(cpu, 150, 250)} title="JARVIS's CPU, % of one core">
              CPU {cpu == null ? "—" : `${Math.round(cpu)}%`}</Chip>
            <Chip tone={tone(load, 8, 14)} title="The machine's load average">
              load {load == null ? "—" : load.toFixed(1)}</Chip>
            <Chip tone={tone(notices, 5, 15)} title="Notifications the page showed last second">
              {notices ?? "—"} notices/s</Chip>
          </span>
        </span>
        {hour && (
          <span className="voice-chips">
            <Chip tone={hour.drops ? "bad" : "ok"}>{hour.drops} drops</Chip>
            <Chip tone={hour.reconnects ? "warn" : "ok"}>{hour.reconnects} reconnects</Chip>
            <Chip tone={hour.errors ? "bad" : "ok"}>{hour.errors} errors</Chip>
            <span className="voice-chip-note">last hour</span>
          </span>
        )}
      </button>
      {record.error && !s && <p className="read-failure">Couldn't read the voice record ({record.error}); trying again.</p>}
    </section>
  );
}
