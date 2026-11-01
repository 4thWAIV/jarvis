// The microphone check. This page runs in JARVIS's page in a sandboxed frame
// with no origin, so it has no microphone and no network of its own: it asks
// JARVIS, by postMessage, to hear the microphone ("listen") and to save each
// result into the package's results folder ("save"). docs/work-packages.md
// has the protocol.
(function () {
  "use strict";

  var SECONDS = 3;
  var NO_SOUND_MS = 2000;
  var pending = {};
  var nextId = 1;
  var recording = null;   // {chunks, samples, sampleRate, timer} while recording
  var clip = null;        // {wav, level} once recorded

  var recordButton = document.getElementById("record");
  var saveButton = document.getElementById("save");
  var levelLine = document.getElementById("level");
  var statusLine = document.getElementById("status");
  var note = document.getElementById("note");

  function ask(message, transfer) {
    message.jarvis = "package";
    parent.postMessage(message, "*", transfer || []);
  }

  function save(name, data) {
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { resolve: resolve, reject: reject };
      ask({ type: "save", id: id, name: name, data: data });
    });
  }

  function say(text, failed) {
    statusLine.textContent = text;
    statusLine.className = failed ? "failed" : "";
  }

  function wav(samples, sampleRate) {
    var out = new DataView(new ArrayBuffer(44 + samples.length * 2));
    function text(at, s) { for (var i = 0; i < s.length; i++) out.setUint8(at + i, s.charCodeAt(i)); }
    text(0, "RIFF"); out.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE");
    text(12, "fmt "); out.setUint32(16, 16, true); out.setUint16(20, 1, true);
    out.setUint16(22, 1, true); out.setUint32(24, sampleRate, true);
    out.setUint32(28, sampleRate * 2, true); out.setUint16(32, 2, true);
    out.setUint16(34, 16, true); text(36, "data"); out.setUint32(40, samples.length * 2, true);
    for (var i = 0; i < samples.length; i++) out.setInt16(44 + i * 2, samples[i], true);
    return out.buffer;
  }

  function decibels(x) {
    return x > 0 ? Math.round(200 * Math.log10(x)) / 10 : null;
  }

  function finish() {
    var r = recording;
    recording = null;
    clearTimeout(r.timer);
    ask({ type: "listen", on: false });
    var samples = new Int16Array(r.samples);
    var at = 0;
    r.chunks.forEach(function (c) {
      var room = samples.length - at;
      if (room > 0) samples.set(c.subarray(0, Math.min(c.length, room)), at);
      at += c.length;
    });
    var peak = 0, sum = 0;
    for (var i = 0; i < samples.length; i++) {
      var v = Math.abs(samples[i]) / 32768;
      if (v > peak) peak = v;
      sum += v * v;
    }
    var level = { seconds: samples.length / r.sampleRate, sample_rate: r.sampleRate,
                  peak_dbfs: decibels(peak), rms_dbfs: decibels(Math.sqrt(sum / samples.length)) };
    clip = { wav: wav(samples, r.sampleRate), level: level };
    levelLine.textContent = "Peak " + level.peak_dbfs + " dBFS, average " + level.rms_dbfs +
      " dBFS over " + level.seconds + " seconds.";
    recordButton.disabled = false;
    saveButton.disabled = false;
  }

  window.addEventListener("message", function (event) {
    if (event.source !== parent) return;
    var m = event.data;
    if (!m || m.jarvis !== "package") return;
    if (m.type === "saved") {
      var p = pending[m.id];
      delete pending[m.id];
      if (p) { if (m.ok) p.resolve(m); else p.reject(new Error(m.error)); }
    } else if (m.type === "listening" && m.on && recording) {
      if (m.microphone === "off") say("JARVIS's microphone is off; turn it on to record.", true);
    } else if (m.type === "audio" && recording) {
      var chunk = new Int16Array(m.pcm);
      recording.sampleRate = m.sampleRate;
      recording.chunks.push(chunk);
      recording.got += chunk.length;
      if (recording.got >= SECONDS * m.sampleRate) {
        recording.samples = SECONDS * m.sampleRate;
        finish();
      }
    }
  });

  recordButton.addEventListener("click", function () {
    recordButton.disabled = true;
    saveButton.disabled = true;
    say("Recording…");
    recording = { chunks: [], got: 0, samples: 0, sampleRate: 16000, timer: setTimeout(function () {
      if (recording && recording.got === 0) {
        recording = null;
        ask({ type: "listen", on: false });
        recordButton.disabled = false;
        say("No sound arrived. Is JARVIS's microphone on?", true);
      }
    }, NO_SOUND_MS) };
    ask({ type: "listen", on: true });
  });

  saveButton.addEventListener("click", function () {
    saveButton.disabled = true;
    say("Saving…");
    var saves = [save("clip.wav", clip.wav), save("level.json", JSON.stringify(clip.level, null, 2))];
    if (note.value.trim()) saves.push(save("note.txt", note.value.trim() + "\n"));
    Promise.all(saves).then(function (saved) {
      say("Saved " + saved.map(function (s) { return s.saved; }).join(", ") +
          ". Tell JARVIS you have finished.");
    }, function (e) {
      saveButton.disabled = false;
      say("Not saved: " + e.message, true);
    });
  });
})();
