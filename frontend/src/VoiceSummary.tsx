/**
 * Voice health at the top of the Diagnostics tab: how well the audio is
 * working, at a glance, at the panel's fixed width.
 *
 *   - the heard ring: the share of his sentences heard in the last hour,
 *     ringed in the status colour, with the status word beside it (Hearing
 *     you, Falling behind, Dropping speech, Not listening), the cause only
 *     when there is a problem, and when speech was last dropped;
 *   - the five stages the audio passes through, each in its colour;
 *   - keeping up: the speed ring (how much of the recogniser's real-time
 *     budget it uses) beside headroom (how far behind recognition is, up to
 *     where speech drops).
 *
 * Red means his speech is at risk, nothing else. The server's timing, CPU,
 * the machine's load, notifications and reconnects are in the Diagnostics
 * view, which clicking this opens. While the page is listening it moves with
 * the health the server sends each second; otherwise it shows the record.
 */

import { useSyncExternalStore } from "react";
import { useTick } from "./WorkflowsView";
import { ago } from "./workflows";
import { HeadroomBar, HeardRing, PipelineTrack, SpeedRing } from "./VoiceVisuals";
import { audioStatus, health, heardShare, openDiagnostics, summary } from "./voiceHealth";

export function VoiceSummary() {
  useTick(1000);
  const live = useSyncExternalStore(health.subscribe, health.get);
  const record = useSyncExternalStore(summary.subscribe, summary.get);
  const s = record.value;
  const status = audioStatus(live?.state ?? s?.state ?? "idle", s?.lastDropAt ?? null);
  const cause = live ? live.cause : s?.cause ?? "";
  const pipeline = live?.pipeline ?? s?.pipeline;
  const last = s?.last2min[s.last2min.length - 1];

  return (
    <section className="voice-summary" aria-label="Voice health">
      <button type="button" className="voice-summary-open" onClick={() => openDiagnostics.set(true)}
              title="Open the Diagnostics view">
        <span className="voice-rings">
          <span className="voice-ring-cap">
            <HeardRing share={s ? heardShare(s.lastHour) : null} tone={status.tone} size={52} />
            <small>heard</small>
          </span>
          <span className="voice-status">
            <b className={`voice-status-word tone-${status.tone}`}>{status.word}</b>
            {status.tone !== "ok" && cause && <span className="voice-cause">{cause}</span>}
            {s?.lastDropAt != null && <span className="voice-last-drop">last drop {ago(s.lastDropAt)}</span>}
          </span>
        </span>
        {pipeline && <PipelineTrack pipeline={pipeline} />}
        <span className="voice-keeping-up">
          <span className="voice-ring-cap">
            <SpeedRing rtf={live ? live.rtf : last?.rtf ?? null} size={40} />
            <small>speed</small>
          </span>
          <HeadroomBar backlogSec={live?.backlogSec ?? last?.backlogSec ?? null} />
        </span>
      </button>
      {record.error && !s && <p className="read-failure">Couldn't read the voice record ({record.error}); trying again.</p>}
    </section>
  );
}
