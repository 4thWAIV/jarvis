# Microphone check

The page records three seconds through JARVIS's microphone. Record 3 seconds starts the recording. The person speaks a sentence; the level display reports its loudness. JARVIS should be muted first when the person does not want their sentence treated as a command. Muting JARVIS does not mute this page's recording.

The person may add a note for the sender. Save results becomes available after recording and saves the clip, measured levels and note in results/clip.wav, results/level.json and results/note.txt. Nothing is sent to the sender by the recording itself.

## Technical assets

index.html supplies these controls and messages. app.js uses the existing JARVIS microphone/save bridge and calculates levels. style.css lays out the form; it carries no additional human content.
