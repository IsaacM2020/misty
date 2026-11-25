import Foundation
import AVFoundation
import Synchronization
#if canImport(PhononTTS)
import PhononTTS
#endif

/// Turns text into mono Float32 audio at `Speaker.sampleRate`, handing chunks over as they're generated.
/// Phonon is the real one; the protocol keeps Speaker compiling where the PhononTTS package isn't linked
/// (`scripts/swift_test.sh` and `scripts/dev_app.sh` use plain swiftc).
public protocol SpeechSynth: Sendable {
    /// Loads the model (slow the first time after an install: Core ML compiles it) and returns the voices.
    func load() async throws -> [String]
    func setVoice(_ name: String) async throws
    /// Returns when the utterance is fully generated. Cancelling the calling task stops it at the next chunk.
    func speak(_ text: String, onAudio: @escaping @Sendable ([Float]) -> Void) async throws
}

/// Misty's mouth. Sentences queue up and play back to back through an `AVAudioPlayerNode` on an engine
/// it is given, so the mic can share that engine and voice processing cancels her own voice.
/// `stopNow()` goes silent at once (it drops scheduled audio and cancels generation).
@MainActor
public final class Speaker {
    public nonisolated static let sampleRate = 24_000.0
    public static let defaultVoice = "default"

    public private(set) var voices: [String] = []
    public private(set) var voice: String
    /// True from the first queued sentence until the last chunk has played (or `stopNow()`).
    public private(set) var isSpeaking = false
    /// Timing only (card A3): audio already handed to the output device, in ms.
    public var outputLatencyMs: Int { Int(engine.outputNode.presentationLatency * 1000) }
    /// Called on the main actor when the queue has fully played out (not after `stopNow()`).
    public var onFinished: (() -> Void)?
    /// Timing only (Season 3 Day 1): "speak_called", "drain_start", "engine_ready", "first_chunk" (+ output latency in ms).
    public var onMark: ((String, Int) -> Void)?
    /// Slate voice sync. A sentence's audio begins `startsIn` seconds from now (its text is as spoken).
    public var onSentenceStart: ((_ text: String, _ startsIn: Double) -> Void)?
    /// The sentence is fully generated, so its audio end is exact: `endsIn` seconds from now, `audioSeconds` long in all.
    public var onSentenceEnd: ((_ text: String, _ endsIn: Double, _ audioSeconds: Double) -> Void)?

    /// How fast she talks, 1.0 = as the voice model makes it. Pitch stays where it is. Isaac found 1.0 a bit quick.
    public static let defaultRate: Float = 0.9
    public static let rateRange: ClosedRange<Float> = 0.7...1.2
    public private(set) var rate: Float

    private let engine: AVAudioEngine
    private let stretch = AVAudioUnitTimePitch()
    private let out = Output()
    private let synth: (any SpeechSynth)?
    private var queue: [String] = []
    private var worker: Task<Void, Never>?
    private var warming: Task<Void, Never>?
    private var pendingBuffers = 0
    private var firstAudioStart: ContinuousClock.Instant?

    public init(engine: AVAudioEngine, synth: (any SpeechSynth)? = Speaker.defaultSynth()) {
        self.engine = engine
        self.synth = synth
        self.voice = UserDefaults.standard.string(forKey: "misty.voice") ?? Speaker.defaultVoice
        let saved = UserDefaults.standard.object(forKey: "misty.voiceRate") as? Float
        self.rate = min(max(saved ?? Speaker.defaultRate, Speaker.rateRange.lowerBound), Speaker.rateRange.upperBound)
        stretch.rate = rate
        out.rate.withLock { $0 = Double(rate) }
        engine.attach(out.node)
        engine.attach(stretch)
        engine.connect(out.node, to: stretch, format: out.format)
        engine.connect(stretch, to: engine.mainMixerNode, format: out.format)
    }

    /// Slower or faster speech, live (the sentence she is saying changes pace too).
    public func setRate(_ r: Float) {
        rate = min(max(r, Self.rateRange.lowerBound), Self.rateRange.upperBound)
        stretch.rate = rate
        out.rate.withLock { $0 = Double(rate) }
        UserDefaults.standard.set(rate, forKey: "misty.voiceRate")
    }

    /// A fixed line for judging a voice and a speed by ear.
    public static let samplePhrase = "Hey Isaac, it's EDITH. This is how I sound. If I'm going too fast, slow me down in Settings."

    public func speakSample() {
        stopNow()
        speak(Self.samplePhrase)
    }

    /// Phonon when the package is linked, otherwise nil (speaking is then a no-op).
    public nonisolated static func defaultSynth() -> (any SpeechSynth)? {
        #if canImport(PhononTTS)
        return DevVoices.enabled ? DevSynth(base: PhononSynth()) : PhononSynth()   // flag: Phonon plus the macOS system voices
        #else
        return DevVoices.enabled ? DevSynth(base: nil) : nil
        #endif
    }

    /// Loads the model and runs one short utterance into the void, so the first real sentence starts fast.
    /// Safe to call more than once; `speak` waits for it.
    public func warm() async {
        if let warming { return await warming.value }
        guard let synth else { return }
        let want = voice
        let t = Task { @MainActor in
            do {
                let start = ContinuousClock.now
                voices = try await synth.load()
                if !voices.contains(want) { voice = voices.contains(Self.defaultVoice) ? Self.defaultVoice : (voices.first ?? want) }
                try await synth.setVoice(voice)
                try await synth.speak("Hi.") { _ in }
                NSLog("VOICE warm ms=%d voices=%d voice=%@", Self.ms(since: start), voices.count, voice)
            } catch {
                NSLog("VOICE warm failed: %@", String(describing: error))
            }
        }
        warming = t
        await t.value
    }

    public func setVoice(_ name: String) async {
        guard let synth, name != voice else { return }
        do {
            try await synth.setVoice(name)
            voice = name
            UserDefaults.standard.set(name, forKey: "misty.voice")
        } catch {
            NSLog("VOICE setVoice %@ failed: %@", name, String(describing: error))
        }
    }

    /// Queues one sentence; it plays right after whatever is already queued.
    public func speak(_ sentence: String) {
        let s = Pronunciation.forSpeech(SpokenText.clean(sentence))   // what the voice model reads badly, as words; her name said EE-dith
        guard !s.isEmpty, synth != nil else { return }
        if !isSpeaking { firstAudioStart = .now; onMark?("speak_called", 0) }
        isSpeaking = true
        queue.append(s)
        if worker == nil { worker = Task { await self.drain() } }
    }

    /// Silence now: drops everything queued and scheduled, and stops generating.
    public func stopNow() {
        out.round.withLock { $0 += 1 }
        queue.removeAll()
        worker?.cancel()
        worker = nil
        pendingBuffers = 0
        firstAudioStart = nil
        isSpeaking = false
        out.clock.withLock { $0.reset() }
        out.node.stop()                  // drops scheduled buffers immediately
        if engine.isRunning { out.node.play() }
    }

    private func drain() async {
        await warm()
        guard let synth else { return }
        onMark?("drain_start", 0)
        let myRound = out.round.withLock { $0 }
        while !queue.isEmpty, !Task.isCancelled {
            let s = queue.removeFirst()
            do {
                try ensurePlaying()
                onMark?("engine_ready", 0)
                let dump = DevDump.folder == nil ? nil : DevDump()
                defer { if let dump, let dir = DevDump.folder {
                    dump.write(to: dir, name: "tts-\(Int(Date().timeIntervalSince1970 * 1000)).wav")
                    NSLog("VOICE dumped %@ voice=%@", s, voice)
                } }
                out.clock.withLock { $0.begin() }
                out.latency.withLock { $0 = engine.outputNode.presentationLatency }
                try await synth.speak(s) { [weak self, out] chunk in
                    dump?.add(chunk)
                    let (ok, startAt) = out.schedule(chunk, round: myRound, played: { Task { @MainActor in self?.played(round: myRound) } },
                                                     latency: out.latency.withLock { $0 })
                    guard ok else { return }
                    Task { @MainActor in self?.scheduled(round: myRound) }
                    if let startAt {
                        Task { @MainActor in
                            guard let self, myRound == self.out.round.withLock({ $0 }) else { return }
                            self.onSentenceStart?(s, max(0, startAt - ProcessInfo.processInfo.systemUptime))
                        }
                    }
                }
                if out.round.withLock({ $0 }) == myRound, let cb = onSentenceEnd {
                    let (end, secs) = out.clock.withLock { ($0.playEnd, $0.seconds) }
                    if secs > 0 { cb(s, max(0, end - ProcessInfo.processInfo.systemUptime), secs) }
                }
            } catch is CancellationError {
                return
            } catch {
                NSLog("VOICE speak failed: %@", String(describing: error))
            }
        }
        guard out.round.withLock({ $0 }) == myRound else { return }
        worker = nil
        if !queue.isEmpty { worker = Task { await self.drain() } } else { finishIfIdle() }
    }

    private func scheduled(round r: Int) {
        guard r == out.round.withLock({ $0 }) else { return }
        pendingBuffers += 1
        if let start = firstAudioStart {
            firstAudioStart = nil
            let outMs = Int(engine.outputNode.presentationLatency * 1000)
            NSLog("VOICE first_audio ms=%d (+%d ms output latency)", Self.ms(since: start), outMs)
            onMark?("first_chunk", outMs)
        }
    }

    private func played(round r: Int) {
        guard r == out.round.withLock({ $0 }) else { return }
        pendingBuffers = max(0, pendingBuffers - 1)
        finishIfIdle()
    }

    private func finishIfIdle() {
        guard isSpeaking, pendingBuffers == 0, queue.isEmpty, worker == nil else { return }
        isSpeaking = false
        onFinished?()
    }

    private func ensurePlaying() throws {
        if !engine.isRunning {
            engine.prepare()
            try engine.start()
        }
        if !out.node.isPlaying { out.node.play() }
    }

    nonisolated static func ms(since start: ContinuousClock.Instant) -> Int {
        let d = ContinuousClock.now - start
        return Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000)
    }

    #if DEBUG
    /// `--speak-test` launch argument: speaks one sentence on a fresh engine and logs the timings
    /// (`VOICE warm`, then `VOICE first_audio` measured from `speak` to the first chunk being scheduled).
    public static func runSpeakTestIfAsked() {
        guard ProcessInfo.processInfo.arguments.contains("--speak-test") else { return }
        let speaker = Speaker(engine: AVAudioEngine())
        Task { @MainActor in
            await speaker.warm()
            speaker.onFinished = { NSLog("VOICE speak-test done"); _ = speaker }
            speaker.speak("Hey Isaac, I'm EDITH. This is how fast I can start talking.")
        }
    }
    #endif
}

/// The player node and the stop counter, touched from Phonon's generation thread as chunks arrive.
/// `AVAudioPlayerNode.scheduleBuffer` is safe from any thread; everything else stays on the main actor.
private final class Output: @unchecked Sendable {
    let node = AVAudioPlayerNode()
    let format = AVAudioFormat(standardFormatWithSampleRate: Speaker.sampleRate, channels: 1)!
    /// Bumped by `stopNow()`; chunks and playback callbacks from an older round are dropped.
    let round = Mutex(0)
    /// Where sentences sit in time (see `SentenceClock`), the playback rate, and the output delay in seconds.
    let clock = Mutex(SentenceClock())
    let rate = Mutex(1.0)
    let latency = Mutex(0.0)

    /// Schedules one chunk unless `stopNow()` has moved on since `round`. Returns whether it was scheduled.
    func schedule(_ samples: [Float], round r: Int, played: @escaping @Sendable () -> Void, latency lat: Double)
        -> (scheduled: Bool, sentenceStart: Double?) {
        guard round.withLock({ $0 }) == r, !samples.isEmpty,
              let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)) else { return (false, nil) }
        buf.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { buf.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        node.scheduleBuffer(buf, completionCallbackType: .dataPlayedBack) { _ in played() }
        let r = rate.withLock { $0 }
        let startAt = clock.withLock {
            $0.add(samples: samples.count, sampleRate: Speaker.sampleRate, rate: r, now: ProcessInfo.processInfo.systemUptime, latency: lat)
        }
        return (true, startAt)
    }
}

#if canImport(PhononTTS)
/// Phonon 31m on the Neural Engine. The model folder ships in the app as `PhononModels` (fetched by
/// `scripts/fetch_voice_models.sh`) and is copied once to Application Support, because Core ML compiles
/// each model next to itself. `MISTY_PHONON_MODELS` points at a writable model folder instead (dev).
actor PhononSynth: SpeechSynth {
    private var tts: Phonon?
    private var loading: Task<Phonon, Error>?

    func load() async throws -> [String] { try await loaded().voices }

    func setVoice(_ name: String) async throws { try await loaded().setVoice(name) }

    func speak(_ text: String, onAudio: @escaping @Sendable ([Float]) -> Void) async throws {
        try await loaded().speak(text, onAudio: onAudio)
    }

    private func loaded() async throws -> Phonon {
        if let tts { return tts }
        if let loading { return try await loading.value }
        let t = Task.detached(priority: .userInitiated) {
            try await Phonon.load(models: try Self.modelsDirectory(), language: .english)
        }
        loading = t
        do {
            let p = try await t.value
            tts = p
            return p
        } catch {
            loading = nil
            throw error
        }
    }

    private static func modelsDirectory() throws -> URL {
        if let dev = ProcessInfo.processInfo.environment["MISTY_PHONON_MODELS"] { return URL(filePath: dev) }
        if let bundled = Bundle.main.url(forResource: "PhononModels", withExtension: nil) {
            return try PhononModels.install(bundled: bundled)
        }
        if PhononModels.isInstalled() { return PhononModels.defaultDirectory }
        throw PhononError(description: "No voice models: run scripts/fetch_voice_models.sh and rebuild")
    }
}
#endif

/// How her name is SAID (never how it is shown). The voices read EDITH as "ED-ith"; this spelling comes out "EE-dith".
/// Isaac's own Mac can try another spelling without a rebuild: `defaults write com.isaacmiranda.Misty misty.edithSpelling -string Eedith`.
public enum Pronunciation {
    public static let defaultSpelling = "Eedith"
    public static var spelling: String { UserDefaults.standard.string(forKey: "misty.edithSpelling") ?? defaultSpelling }

    public static func forSpeech(_ text: String, spelling: String = Pronunciation.spelling) -> String {
        text.replacingOccurrences(of: #"\bedith\b"#, with: spelling, options: [.regularExpression, .caseInsensitive])
    }
}
