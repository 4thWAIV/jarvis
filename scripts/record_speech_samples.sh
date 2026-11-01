#!/bin/bash
# Record spoken sentences as 16 kHz mono WAV clips, each with its reference
# transcript beside it, then measure them with scripts/bench_speech_input.py.
#
#   scripts/record_speech_samples.sh OUT_DIR --model MODEL_DIR [--model ...]
#       [--sentences FILE] [--seconds N] [--mic DEVICE] [--say VOICE]
#
# OUT_DIR must be outside the repository; it is created if missing. Each
# sentence (one per line of FILE, the Spanish commands below by default) is
# shown, recorded from the default microphone for N seconds (5) once you
# press Enter, and saved as NN.wav with NN.txt. The microphone is read with
# ffmpeg's avfoundation input; the first run asks for microphone permission
# for the terminal. --mic picks the input by name or number (the system's
# default input otherwise); `ffmpeg -f avfoundation -list_devices true -i ""`
# lists them.
#
# --say VOICE uses macOS's `say` with that voice in place of the microphone
# (no keys pressed, no fixed length): a test of everything but the
# microphone, and the way synthetic clips are made.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
SECONDS_EACH=5
SENTENCES=""
VOICE=""
MIC="default"
MODELS=()
OUT=""

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODELS+=(--model "$2"); shift 2 ;;
    --sentences) SENTENCES="$2"; shift 2 ;;
    --seconds) SECONDS_EACH="$2"; shift 2 ;;
    --mic) MIC="$2"; shift 2 ;;
    --say) VOICE="$2"; shift 2 ;;
    -h|--help) usage ;;
    -*) echo "Unknown option $1" >&2; usage ;;
    *) [ -z "$OUT" ] || usage; OUT="$1"; shift ;;
  esac
done
[ -n "$OUT" ] && [ ${#MODELS[@]} -gt 0 ] || usage

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
case "$OUT/" in
  "$(cd "$REPO" && pwd -P)/"*)
    rmdir "$OUT" 2>/dev/null || true
    echo "$OUT is inside the repository; choose a folder outside it." >&2; exit 2 ;;
esac

if [ -z "$VOICE" ] && ! command -v ffmpeg >/dev/null; then
  echo "Recording needs ffmpeg (brew install ffmpeg), or use --say VOICE." >&2
  exit 1
fi

spanish_commands() {
  cat <<'EOF'
sí
no
cancela
empezar de cero
sí, adelante
espérate un momento
¿qué agentes están trabajando ahora?
léeme el último mensaje del agente de diagnósticos
mándale un mensaje al agente guardia preguntando cómo va
¿tengo mensajes nuevos?
recuérdame a las tres y media llamar al banco
no reinicies el servidor hasta que pasen las pruebas
¿cuántos agentes terminaron hoy?
pon un temporizador de quince minutos
son las cinco y cuarto de la tarde
EOF
}

if [ -n "$SENTENCES" ]; then LINES="$(cat "$SENTENCES")"; else LINES="$(spanish_commands)"; fi
TOTAL=$(printf '%s\n' "$LINES" | grep -c .)

record() {  # record SENTENCE WAV
  if [ -n "$VOICE" ]; then
    local aiff="$OUT/.say.aiff"
    say -v "$VOICE" -o "$aiff" "$1"
    afconvert -f WAVE -d LEI16@16000 -c 1 "$aiff" "$2"
    rm -f "$aiff"
  else
    ffmpeg -loglevel error -y -f avfoundation -i ":$MIC" -t "$SECONDS_EACH" \
      -ac 1 -ar 16000 -sample_fmt s16 "$2" </dev/null
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
    [ -z "$VOICE" ] || break
    read -r -p "Enter for the next one, r to record it again: " again </dev/tty
    [ "$again" = "r" ] || break
  done
done <<< "$LINES"

echo
echo "Saved $n clips in $OUT. Measuring them:"
"$PYTHON" "$REPO/scripts/bench_speech_input.py" --threads 2 "${MODELS[@]}" --clips "$OUT"
