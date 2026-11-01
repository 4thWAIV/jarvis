#!/bin/bash
# Record each sentence of sentences.txt as results/clips/NN.wav (16 kHz mono
# 16-bit), with its reference text beside it in NN.txt.
#
#   bash scripts/record.sh [--seconds N] [--say VOICE]
#
# Run it in a Terminal window from the package's folder. Each sentence is
# shown; press Enter, say it, and it records for N seconds (5) with
# scripts/record.js (macOS's own AVFoundation, nothing to install). A clip
# with no sound in it is offered again: macOS records silence when Terminal
# is not allowed the microphone (System Settings, Privacy & Security,
# Microphone).
#
# --say VOICE makes each clip with macOS's `say` in that voice instead of the
# microphone (no keys, no microphone): a test of everything else.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${JARVIS_PYTHON:-python3}"
SECONDS_EACH=5
VOICE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --seconds) SECONDS_EACH="$2"; shift 2 ;;
    --say) VOICE="$2"; shift 2 ;;
    *) echo "Unknown option $1" >&2; exit 2 ;;
  esac
done

OUT="$HERE/results/clips"
mkdir -p "$OUT"
TOTAL=$(grep -c . "$HERE/sentences.txt")

# The loudest sample in a 16-bit WAV, out of 32767.
peak() {
  "$PYTHON" -c 'import sys, wave, array
w = wave.open(sys.argv[1]); a = array.array("h", w.readframes(w.getnframes()))
print(max((abs(x) for x in a), default=0))' "$1"
}

record() {  # record SENTENCE WAV
  if [ -n "$VOICE" ]; then
    local aiff="$OUT/.say.aiff"
    say -v "$VOICE" -o "$aiff" "$1"
    afconvert -f WAVE -d LEI16@16000 -c 1 "$aiff" "$2"
    rm -f "$aiff"
  else
    osascript -l JavaScript "$HERE/scripts/record.js" "$2" "$SECONDS_EACH" >/dev/null
  fi
}

n=0
while IFS= read -r sentence; do
  [ -n "$sentence" ] || continue
  n=$((n + 1))
  name="$(printf '%02d' "$n")"
  while true; do
    echo
    echo "[$n/$TOTAL]  $sentence"
    if [ -z "$VOICE" ]; then
      read -r -p "Press Enter, then say it ($SECONDS_EACH s)... " </dev/tty
      echo "Recording."
    fi
    record "$sentence" "$OUT/$name.wav"
    printf '%s\n' "$sentence" > "$OUT/$name.txt"
    if [ "$(peak "$OUT/$name.wav")" -lt 300 ]; then
      echo "That clip is silent. Is Terminal allowed the microphone (System Settings,"
      echo "Privacy & Security, Microphone)?"
      [ -z "$VOICE" ] || exit 1
      read -r -p "Enter to record it again: " </dev/tty
      continue
    fi
    if [ -z "$VOICE" ]; then
      read -r -p "Enter for the next one, r to record it again: " again </dev/tty
      [ "$again" = "r" ] && continue
    fi
    break
  done
done < "$HERE/sentences.txt"

echo
echo "Saved $n clips in $OUT. Recording is finished; you can switch JARVIS's"
echo "microphone back on and tell him."
