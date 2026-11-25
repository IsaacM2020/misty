import Foundation
#if canImport(SherpaOnnxC)
import SherpaOnnxC
#endif

// "Edith", said out loud, starts the same voice session the hotkey opens.
//
// A tiny open-vocabulary keyword spotter (sherpa-onnx, a 3.3M-parameter streaming zipformer, on the CPU, about 6 MB of
// files) listens to the mic and nothing else: no words are written down, no audio is kept or sent anywhere. This file is
// the pure part: the setting, the gate that says when listening is allowed, the tuning, the keyword tokens, and the
// spotter itself. `WakeListener.swift` owns the mic and the hand-off to `VoiceVM`.

/// The `wake_word` setting. Stored by the engine (config.py DEFAULTS), read by the app after it connects.
public struct WakeSettings: Codable, Equatable, Sendable {
    public var enabled: Bool
    public var word: String
    /// 0...1. Higher wakes on a fainter or less clear "Edith" and also false-wakes more. 0.5 is tuned on his own recorded hours.
    public var sensitivity: Double

    public init(enabled: Bool = true, word: String = "edith", sensitivity: Double = 0.5) {
        self.enabled = enabled; self.word = word; self.sensitivity = sensitivity
    }
    public static let standard = WakeSettings()

    /// A word the spotter has tokens for. Others are refused rather than guessed (see `WakeKeywords`).
    public var wordSupported: Bool { WakeKeywords.lines(for: word) != nil }
}

/// When the mic may be listened to for the wake word at all, and when a detection may start a session. One struct so the
/// two checks can never disagree: the listener closes the mic when this closes, and `VoiceVM.wake()` asks again.
public struct WakeGate: Equatable, Sendable {
    public var enabled = true            // the setting
    public var modelReady = true         // the spotter model and library are here
    public var micAllowed = true         // the mic permission he already gave; this never asks
    public var voiceIdle = true          // no voice session: not starting, listening, thinking or speaking
    public var speaking = false          // her own voice is playing, or was just now (its echo says "Edith" too)
    public var turnRunning = false       // a tap-to-talk exchange or a spoken update is under way
    public var inClass = false           // class mode, or a class open or being recorded
    public var ambient = false           // always-listening mode already holds the mic and answers to her name
    public var dictating = false         // push-to-talk dictation is recording
    public var inCall = false            // a call app has the mic
    public var paused = false            // "Pause everything"

    public init() {}

    /// The first reason it is closed, for the log; nil when open.
    public var blocker: String? {
        if !enabled { return "off" }
        if paused { return "paused" }
        if !modelReady { return "no model" }
        if !micAllowed { return "no mic permission" }
        if inClass { return "class" }
        if inCall { return "call" }
        if ambient { return "ambient" }
        if dictating { return "dictating" }
        if speaking { return "speaking" }
        if turnRunning { return "turn" }
        if !voiceIdle { return "voice busy" }
        return nil
    }
    public var open: Bool { blocker == nil }
}

/// Sensitivity to the spotter's two knobs. `score` is the bonus given to the keyword's path (higher fires easier);
/// `threshold` is the acoustic probability needed (lower fires easier). Measured with scripts/checks/wake_false.py on 24.7 hours of
/// his recorded class audio (mic and Mac-sound tracks) and 30 synthetic "Edith" clips: at 0.5, 0.41 false wakes an hour of
/// class audio. A sweep of the two knobs showed the false wakes barely move with them (the model confuses a few sounds with the
/// name at any setting), so the real filter is the one in `WakeStream`: the name must start an utterance.
public enum WakeTuning {
    public static func params(sensitivity: Double) -> (score: Float, threshold: Float) {
        let s = min(1, max(0, sensitivity))
        // s = 0 wakes only on a clear, loud "Edith"; s = 1 is the loosest the spotter is worth running at.
        // Synthetic clips caught, of 30, with the false wakes an hour on 6 h of his audio (before the utterance rule):
        // (1.5, 0.15) 11 and 0.16/h; (2.0, 0.05) 20 and 0.33/h; (2.5, 0.017) 20 and 0.65/h. Threshold falls geometrically.
        let threshold = 0.15 * pow(1.0 / 9.0, s)   // 0.15 ... 0.05 at 0.5 ... 0.017
        let score = 1.5 + s                        // 1.5 ... 2.0 at 0.5 ... 2.5
        return (Float(score), Float(threshold))
    }
}

/// The keyword as the model wants it: BPE pieces, one line per way the word can be cut, all sharing one label. The model has
/// no tokenizer in the app, so a word needs a line here before it can be a wake word. "edith" is the one that ships.
public enum WakeKeywords {
    static let table: [String: [String]] = [
        "edith": ["▁E DI TH", "▁ ED I TH", "▁ ED IT H", "▁E DI T H"],
        "misty": ["▁MI S TY", "▁ M IS TY", "▁MI S T Y", "▁ M IST Y"],
    ]

    public static func lines(for word: String) -> [String]? { table[word.lowercased().trimmingCharacters(in: .whitespaces)] }

    /// The text the spotter reads as its keyword list.
    public static func buffer(for word: String) -> String? {
        guard let l = lines(for: word) else { return nil }
        let label = word.uppercased()
        return l.map { "\($0) @\(label)" }.joined(separator: "\n") + "\n"
    }
}

/// Where the spotter's files are: `<home>/models/wake/<model>/`, put there by scripts/fetch_wake_model.sh.
public enum WakeModel {
    public static let folder = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
    static let encoder = "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
    static let decoder = "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
    static let joiner = "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
    static let tokens = "tokens.txt"

    public static func dir(home: String) -> URL { URL(fileURLWithPath: home).appendingPathComponent("models/wake/\(folder)") }

    public static func installed(home: String) -> Bool {
        let d = dir(home: home)
        return [encoder, decoder, joiner, tokens].allSatisfy { FileManager.default.fileExists(atPath: d.appendingPathComponent($0).path) }
    }

    /// True when this build has the sherpa-onnx library in it (scripts/fetch_wake_model.sh ran before the build).
    public static var compiledIn: Bool {
        #if canImport(SherpaOnnxC)
        return true
        #else
        return false
        #endif
    }
}

/// The keyword was heard. `endSeconds` is when the word itself ended, in seconds of audio fed since the last `reset`
/// (nil when the spotter cannot say): the model reports a word well after it is spoken, so what he said next is already past.
public struct WakeHit: Equatable, Sendable {
    public var endSeconds: Double?
    public init(endSeconds: Double? = nil) { self.endSeconds = endSeconds }
}

/// Feeds 16 kHz mono Float32 audio in, says when the keyword was heard. Used from one thread at a time.
public protocol WakeSpotter: AnyObject, Sendable {
    /// A hit when the keyword has just been completed in what was fed so far. The stream starts afresh after a hit.
    func accept(_ samples: [Float]) -> WakeHit?
    /// Forget everything heard so far (new audio, not continuing the old).
    func reset()
}

#if canImport(SherpaOnnxC)
/// sherpa-onnx's streaming keyword spotter through its C API (linked statically, see app/project.yml).
public final class SherpaSpotter: WakeSpotter, @unchecked Sendable {
    private let spotter: OpaquePointer
    private var stream: OpaquePointer?
    private let lock = NSLock()

    /// nil when the files are missing, the word has no tokens, or the library refuses the model.
    public init?(home: String, settings: WakeSettings, tuning: (score: Float, threshold: Float)? = nil) {
        guard WakeModel.installed(home: home), let kw = WakeKeywords.buffer(for: settings.word) else { return nil }
        let d = WakeModel.dir(home: home)
        let tune = tuning ?? WakeTuning.params(sensitivity: settings.sensitivity)
        let strings = [d.appendingPathComponent(WakeModel.encoder).path, d.appendingPathComponent(WakeModel.decoder).path,
                       d.appendingPathComponent(WakeModel.joiner).path, d.appendingPathComponent(WakeModel.tokens).path,
                       "cpu", kw].map { strdup($0)! }
        defer { strings.forEach { free($0) } }
        var cfg = SherpaOnnxKeywordSpotterConfig()
        cfg.feat_config.sample_rate = 16000
        cfg.feat_config.feature_dim = 80
        cfg.model_config.transducer.encoder = UnsafePointer(strings[0])
        cfg.model_config.transducer.decoder = UnsafePointer(strings[1])
        cfg.model_config.transducer.joiner = UnsafePointer(strings[2])
        cfg.model_config.tokens = UnsafePointer(strings[3])
        cfg.model_config.provider = UnsafePointer(strings[4])
        cfg.model_config.num_threads = 1                       // one thread: it must stay far under a core
        cfg.max_active_paths = 4
        cfg.num_trailing_blanks = 1
        cfg.keywords_score = tune.score
        cfg.keywords_threshold = tune.threshold
        cfg.keywords_buf = UnsafePointer(strings[5])
        cfg.keywords_buf_size = Int32(kw.utf8.count)
        guard let k = SherpaOnnxCreateKeywordSpotter(&cfg) else { return nil }
        spotter = k
        stream = SherpaOnnxCreateKeywordStream(k)
        if stream == nil { SherpaOnnxDestroyKeywordSpotter(k); return nil }
    }

    deinit {
        if let s = stream { SherpaOnnxDestroyOnlineStream(s) }
        SherpaOnnxDestroyKeywordSpotter(spotter)
    }

    public func accept(_ samples: [Float]) -> WakeHit? {
        lock.lock(); defer { lock.unlock() }
        guard let s = stream, !samples.isEmpty else { return nil }
        samples.withUnsafeBufferPointer { SherpaOnnxOnlineStreamAcceptWaveform(s, 16000, $0.baseAddress, Int32($0.count)) }
        var hit: WakeHit?
        while SherpaOnnxIsKeywordStreamReady(spotter, s) == 1 {
            SherpaOnnxDecodeKeywordStream(spotter, s)
            if let r = SherpaOnnxGetKeywordResult(spotter, s) {
                if let k = r.pointee.keyword, k.pointee != 0 {
                    let n = Int(r.pointee.count)
                    // the last token's time is when the model emitted the word's end, relative to the first sample fed
                    hit = WakeHit(endSeconds: n > 0 ? r.pointee.timestamps.map { Double($0[n - 1]) } : nil)
                    SherpaOnnxResetKeywordStream(spotter, s)
                }
                SherpaOnnxDestroyKeywordResult(r)
            }
        }
        return hit
    }

    public func reset() {
        lock.lock(); defer { lock.unlock() }
        if let s = stream { SherpaOnnxDestroyOnlineStream(s) }
        stream = SherpaOnnxCreateKeywordStream(spotter)
    }
}
#endif

/// Builds the real spotter for a setting, or nil (library not built in, files missing, unknown word).
public enum WakeSpotterFactory {
    public static func make(home: String, settings: WakeSettings) -> WakeSpotter? {
        #if canImport(SherpaOnnxC)
        return SherpaSpotter(home: home, settings: settings)
        #else
        return nil
        #endif
    }
}

/// How a wake-started session ends without a second tap: he says his piece and goes quiet, or says nothing at all.
/// Pure and fed audio in order, so it is tested with synthetic sound. The first `deaf` seconds are not judged: the chime
/// plays there and the mic hears it.
public struct WakeTurn: Sendable {
    public enum Decision: Equatable, Sendable { case keep, send, giveUp }

    public var deaf = 0.35                // chime, and the tail of "Edith" itself
    public var quietEnds = 1.0            // quiet after he has spoken that ends the turn
    public var noSpeechGivesUp = 6.0      // no words at all within this long: close quietly
    public var longest = 45.0             // a hard stop, sent as it is
    private var ep = Endpointer()
    private var heard = 0.0               // seconds fed
    private var spoke = false
    private var done = false

    public init() { ep.closeTalk(); ep.endMs = 1000 }

    public var speechSeen: Bool { spoke }

    public mutating func feed(_ samples: [Float]) -> Decision {
        guard !done else { return .keep }
        let before = heard
        heard += Double(samples.count) / Double(Endpointer.rate)
        if heard > deaf {                                  // only the part past the deaf window is judged
            let skip = min(samples.count, max(0, Int((deaf - before) * Double(Endpointer.rate))))
            for e in ep.feed(skip > 0 ? Array(samples[skip...]) : samples) {
                switch e {
                case .started: spoke = true
                case .ended: done = true; return .send
                }
            }
        }
        if heard >= longest { done = true; return spoke ? .send : .giveUp }
        if !spoke, heard >= noSpeechGivesUp { done = true; return .giveUp }
        return .keep
    }
}

/// Is there sound worth listening to? The spotter costs the same on silence as on speech, and an idle desk is mostly silence,
/// so it only hears audio while this says active: from a sustained rise above the room's noise floor until `hangover` seconds
/// after the last one (long enough for the model to finish deciding about a word that was the last thing said).
/// Loudness against a moving floor, like `Endpointer`: a steady fan stops counting as sound after a few seconds.
public struct WakeActivity: Sendable {
    public var hangover = 1.5
    public var startFrames = 3                      // 20 ms frames above the bar in a row: a spoken syllable, not a click
    public var minLevel: Float = 0.0015
    public var ratio: Float = 3.0
    private var floor: Float = 0.002
    private var pending: [Float] = []
    private var loudRun = 0
    private var sinceLoud = Int.max / 2             // frames since the last confirmed sound
    public private(set) var active = false
    static let frame = 320
    /// Where each recent utterance began, in samples since the first one fed: a sustained sound after at least `pauseFrames`
    /// of not-loud. A name called out starts one ("Edith, ..."); one buried in the middle of running speech does not.
    public private(set) var onsets: [Int] = []
    public var pauseFrames = 25                     // 0.5 s
    private var framesSeen = 0
    private var quietRun = 25                       // not-loud frames in a row, right up to now (the mic opening counts as quiet)
    private var quietBeforeRun = 0                  // how long it was quiet before the current loud run began

    public init() {}

    /// Feed audio in order; true while the spotter should be hearing it.
    public mutating func feed(_ samples: [Float]) -> Bool {
        pending += samples
        var i = 0
        while pending.count - i >= Self.frame {
            var sum: Float = 0
            for k in i..<(i + Self.frame) { sum += pending[k] * pending[k] }
            i += Self.frame
            let rms = (sum / Float(Self.frame)).squareRoot()
            let loud = rms > max(minLevel, floor * ratio)
            floor = rms < floor ? floor * 0.9 + rms * 0.1 : min(floor * 1.002, max(rms, floor))
            framesSeen += 1
            if loud { if loudRun == 0 { quietBeforeRun = quietRun }; quietRun = 0 } else { quietRun += 1 }
            loudRun = loud ? loudRun + 1 : 0
            if loudRun == startFrames, quietBeforeRun >= pauseFrames {
                onsets.append((framesSeen - startFrames) * Self.frame)
                if onsets.count > 8 { onsets.removeFirst() }
            }
            if loudRun >= startFrames { sinceLoud = 0 } else if sinceLoud < Int.max / 4 { sinceLoud += 1 }
            active = Double(sinceLoud) * 0.02 <= hangover
        }
        pending.removeFirst(i)
        return active
    }
}

/// The audio-thread half of the listener: the spotter behind an activity gate, a short memory of the last seconds of sound,
/// and the words heard after a detection.
final class WakeStream: @unchecked Sendable {
    private let lock = NSLock()
    private var spotter: WakeSpotter?
    private var activity = WakeActivity()
    private let gated: Bool
    private var live = false                  // the spotter is hearing a continuous stretch of audio
    private var base = 0                      // sample index (in `total` terms) of the spotter's time zero
    private var total = 0                     // samples seen since the mic opened
    private var ring: [Float] = []            // the last few seconds, always kept (a few hundred KB), so a late hit can hand back what followed it
    private var tail: [Float] = []
    private var collecting = false
    private var fired = false                 // one detection per stream until the mic is reopened
    private(set) var rejected = 0             // hits dropped for not being at the start of an utterance
    private let onHit: @Sendable () -> Void
    /// The name must end this soon after an utterance began ("Edith" or "Hey Edith", then the question). The same word in the
    /// middle of running speech is far more often someone else's talk than a call.
    static let nameWithin = 1.8
    static let tailMax = 16000 * 10
    static let ringMax = 16000 * 4
    static let preroll = 16000 * 6 / 10       // fed ahead of the moment the gate opened: the word's own first sounds

    init(spotter: WakeSpotter, gated: Bool = true, onHit: @escaping @Sendable () -> Void) {
        self.spotter = spotter; self.gated = gated; self.onHit = onHit
    }

    func feed(_ samples: [Float]) {
        lock.lock()
        total += samples.count
        ring.append(contentsOf: samples)
        if ring.count > Self.ringMax * 5 / 4 { ring.removeFirst(ring.count - Self.ringMax) }
        if collecting, tail.count < Self.tailMax { tail.append(contentsOf: samples) }
        guard let sp = spotter, !fired else { lock.unlock(); return }
        var heard = samples
        if gated {
            let open = activity.feed(samples)
            if open {
                if !live {                    // sound just began: a fresh stretch, starting a little before it
                    live = true
                    sp.reset()
                    let pre = min(ring.count, Self.preroll + samples.count)
                    base = total - pre
                    heard = Array(ring.suffix(pre))
                }
            } else {
                live = false
                lock.unlock(); return
            }
        } else if !live { live = true; base = total - samples.count }
        var hit = sp.accept(heard)
        if gated, let h = hit, let e = h.endSeconds {
            let end = base + Int(e * 16000)
            let began = activity.onsets.last { $0 <= end }
            if began == nil || Double(end - began!) > Self.nameWithin * 16000 { rejected += 1; hit = nil }
        }
        if let hit {
            fired = true
            collecting = true
            // what he said after the word: from where the word ended (when known) to now
            var from = total
            if let e = hit.endSeconds { from = min(total, max(base + Int(e * 16000), total - ring.count)) }
            tail = Array(ring.suffix(total - from))
        }
        lock.unlock()
        if hit != nil { onHit() }
    }

    var hasFired: Bool { lock.lock(); defer { lock.unlock() }; return fired }
    var tailPending: Bool { lock.lock(); defer { lock.unlock() }; return collecting }

    func takeTail() -> [Float] {
        lock.lock(); defer { lock.unlock() }
        let t = tail
        tail = []; collecting = false
        return t
    }

    /// Back to listening: the detection was dropped or the session never took hold.
    func clearTail() {
        lock.lock()
        tail = []; collecting = false; fired = false; live = false
        spotter?.reset()
        lock.unlock()
    }
    func testFire() { lock.lock(); fired = true; collecting = true; tail = []; lock.unlock() }
    func close() { lock.lock(); spotter = nil; tail = []; collecting = false; ring = []; lock.unlock() }
}
