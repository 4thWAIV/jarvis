# Spanish speech test

You are running this package with the person in front of you. It measures how
well the Spanish Kroko speech model hears them: they say 15 short Spanish
commands, the clips are measured with their own JARVIS speech engine, and
the results go back to whoever sent the package. It takes about ten minutes
and downloads about 120 MB. Run every step with `run_package_command` on this
package, and only once they have said to go ahead.

## 1. Say what it is

Tell them in a sentence or two: 15 Spanish phrases, about ten minutes, a
120 MB download, the recordings are sent back to the sender. Ask whether to
start.

## 2. Fetch the Spanish model

Run `"$JARVIS_PYTHON" scripts/fetch_model.py`. It downloads the model into this
package's folder and checks its SHA-256
(`31b2230a95d23290b308b393da930015a4b2105cb3abb9367aed35f7fcf29cf1`, release
`sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06`). Nothing is installed
into JARVIS. If it says "Refused", stop and tell them.

## 3. Record

1. Ask them to pause your listening first, with the microphone button on your
   page: otherwise you hear the Spanish too and take it as words to you. With
   it paused they cannot talk to you; they turn it back on when the recording
   is finished.
2. Run `bash scripts/record.sh` with `terminal: true`. A Terminal window shows
   each sentence; they press Enter, say it, and it records five seconds. The
   first recording asks to allow Terminal the microphone; they must allow it.
   A silent clip is offered again.
3. Wait. When they are back and say it is done, go on.

## 4. Measure

Run `"$JARVIS_PYTHON" scripts/bench.py`. It writes `results/summary.md` and
`results/bench.json`. Tell them the word error rate in one sentence (it is in
the output); do not read the table aloud.

## 5. Send the results back

Call `package_results` for this package, then `draft_message` replying to the
message the package came in (`reply_to`), with a one-line text such as
"Spanish speech test results" and every path `package_results` gave in
`files`. Then `request_message_review`, offer to read it back, and send it
only on their yes.


## Supplied content

### sentences.txt

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

The scripts are technical support: fetch_model.py obtains the checked model, record.sh prompts and records each sentence, and bench.py measures the recordings. The instructions above describe their inputs, outputs and approval points.
