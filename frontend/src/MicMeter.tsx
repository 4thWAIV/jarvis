/**
 * The microphone level as a segmented meter: segments light from the left as
 * the volume rises, from a very light green (too quiet to make out) through
 * green and amber to red, like a regular mic meter.
 * If it moves when you speak, the microphone is working.
 */

import { useSyncExternalStore } from "react";
import { micLevel } from "./mic";

const SEGMENTS = 16;
// The level is an RMS amplitude (0..1). Meters read in decibels: a quiet room
// sits near the floor and normal speech lands in the middle of the rail.
const FLOOR_DB = -50;
const CEILING_DB = -12;

function litSegments(rms: number): number {
  const db = 20 * Math.log10(Math.max(rms, 1e-6));
  const fraction = (db - FLOOR_DB) / (CEILING_DB - FLOOR_DB);
  return Math.round(Math.min(1, Math.max(0, fraction)) * SEGMENTS);
}

// The first few segments light for a signal too quiet to make out.
const FAINT_SEGMENTS = 3;

function zone(index: number): string {
  if (index < FAINT_SEGMENTS) return "faint";
  const position = (index + 1) / SEGMENTS;
  if (position > 0.85) return "high";
  if (position > 0.6) return "mid";
  return "low";
}

export function MicMeter() {
  const lit = litSegments(useSyncExternalStore(micLevel.subscribe, micLevel.get));
  return (
    <div id="mic-meter" title="microphone input">
      {Array.from({ length: SEGMENTS }, (_, i) => (
        <span key={i} className={`mic-segment ${zone(i)}${i < lit ? " on" : ""}`} />
      ))}
    </div>
  );
}
