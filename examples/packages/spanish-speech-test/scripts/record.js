// Record the default microphone into a 16 kHz mono 16-bit WAV, with nothing
// to install: macOS's own AVFoundation, from JavaScript for Automation.
//
//   osascript -l JavaScript scripts/record.js OUT.wav SECONDS
//   osascript -l JavaScript scripts/record.js --check OUT.wav
//
// The first recording asks for microphone permission for the app it runs in
// (Terminal). Refused, macOS records silence rather than failing, so
// record.sh checks every clip for sound. --check builds the recorder with
// these settings and records nothing: a test of everything but the
// microphone.
ObjC.import("AVFoundation");
ObjC.import("Foundation");

// kAudioFormatLinearPCM, the four characters 'lpcm'.
const LINEAR_PCM = 0x6c70636d;

function settings() {
  // The AV*Key constants are strings equal to their own names.
  const s = $.NSMutableDictionary.alloc.init;
  s.setObjectForKey($.NSNumber.numberWithUnsignedInt(LINEAR_PCM), $("AVFormatIDKey"));
  s.setObjectForKey($.NSNumber.numberWithDouble(16000), $("AVSampleRateKey"));
  s.setObjectForKey($.NSNumber.numberWithInt(1), $("AVNumberOfChannelsKey"));
  s.setObjectForKey($.NSNumber.numberWithInt(16), $("AVLinearPCMBitDepthKey"));
  s.setObjectForKey($.NSNumber.numberWithBool(false), $("AVLinearPCMIsFloatKey"));
  s.setObjectForKey($.NSNumber.numberWithBool(false), $("AVLinearPCMIsBigEndianKey"));
  s.setObjectForKey($.NSNumber.numberWithBool(false), $("AVLinearPCMIsNonInterleaved"));
  return s;
}

function recorder(path) {
  const error = Ref();
  // The .wav extension makes it a WAVE file.
  const rec = $.AVAudioRecorder.alloc.initWithURLSettingsError(
    $.NSURL.fileURLWithPath(path), settings(), error);
  if (!rec || rec.isNil()) {
    const why = error[0] && !error[0].isNil() ? error[0].localizedDescription.js : "unknown";
    throw new Error("The recorder could not be made: " + why);
  }
  return rec;
}

function run(argv) {
  if (argv[0] === "--check") {
    // Its settings and format read back empty until it is prepared, and
    // preparing opens the microphone, so this is as far as a check goes.
    const rec = recorder(argv[1]);
    if (rec.isRecording || !rec.respondsToSelector("recordForDuration:")) {
      throw new Error("The recorder is not usable.");
    }
    return "ready: 16 kHz mono 16-bit WAV to " + rec.url.path.js;
  }
  const path = argv[0];
  const seconds = parseFloat(argv[1] || "5");
  if (!path || !(seconds > 0)) {
    throw new Error("usage: record.js OUT.wav SECONDS");
  }
  const rec = recorder(path);
  if (!rec.prepareToRecord || !rec.recordForDuration(seconds)) {
    throw new Error("Recording did not start; is a microphone connected and allowed?");
  }
  const until = Date.now() + seconds * 1000 + 2000;
  while (rec.isRecording && Date.now() < until) {
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
  }
  rec.stop;
  return path;
}
