import Foundation
#if canImport(PhononCoreML)
import PhononCoreML
#endif

/// What the ears tell the voice loop.
public enum STTFrame: Equatable, Sendable {
    case partial(String)      // the whole current guess for the phrase in flight; replaces the last one
    case final(String)        // a finished phrase, after Isaac goes quiet
    case error(String)
}

/// Misty's ears: Phonon-2 (Fermion Research) on the Neural Engine, inside the app. The mic's samples go through the
/// Endpointer; while he talks the phrase so far is re-read every `partialEvery` for live words, and when he stops the
/// whole phrase is read once more for the final text. A read of a few seconds takes ~0.1-0.2 s; the model sits at
/// ~115 MB and nothing goes through Python or the network.
public final class Ears: @unchecked Sendable {
    public static var modelURL: URL {
        if let p = ProcessInfo.processInfo.environment["MISTY_EARS_MODEL"] { return URL(fileURLWithPath: p) }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Misty/Phonon-2-CoreML")
    }

    public var onFrame: (@Sendable (STTFrame) -> Void)?
    /// How often the phrase in flight is re-read: often while she speaks (a "stop" must land fast), less while she
    /// listens, because live mode hears the room all day and every read is Neural Engine work.
    static let partialSpeaking = 0.4
    static let partialListening = 0.8
    /// Live mode hears the room all day and nobody sees its partials (only her name and a "stop" matter), so it reads less
    /// often and only the last few seconds: re-reading the whole growing phrase every 0.8 s kept the app near 70% CPU.
    static let partialLean = 1.6
    static let leanWindowSamples = Endpointer.rate * 4
    private var partialEvery = partialListening     // vadQ
    private var speaking = false                    // vadQ
    private var lean = false                        // vadQ

    private let vadQ = DispatchQueue(label: "misty.ears.vad")
    private let readQ: DispatchQueue
    private var endpointer = Endpointer()            // only touched on vadQ
    private var lastPartial = 0.0                    // vadQ
    private var sameText = ("", 0)                   // vadQ: the last partial and how many reads in a row gave it
    static let settledReads = 3                      // that many identical partials (~1.2-2.4 s) and the phrase is done
    private var emptyReads = 0                       // vadQ: partial reads in a row with no words
    static let noiseReads = 4                        // ~2-3 s of sound with no words in it is the room, not Isaac
    private var reading = false                      // vadQ: a partial read is in flight, skip the next one
    private var earlyOn = true                       // vadQ: F2, off in class mode
    private var earlyTried = false                   // vadQ: the early read for this quiet run was made
    private var earlyAtStore: Date?                  // when a phrase last ended early (read from the main thread)
    /// F2: when the last phrase ended early (350 ms of quiet and the read looked finished), for the merge count.
    public var lastEarlyEnd: Date? { lock.lock(); defer { lock.unlock() }; return earlyAtStore }
    /// Test hook: reads with this instead of the model.
    var testReader: (@Sendable ([Float]) -> String)?
    private var session = 0                          // vadQ: bumped on reset so a late read can't leak into a new session
    #if canImport(PhononCoreML)
    private var model: Transcriber?                  // readQ
    #endif

    private let shared: Ears?                      // the Ears whose loaded model this one reads with (class: the Mac's audio)

    // ---- chunked reading: while he talks, whatever he has already said is read by Fluid Speech Mini at each short pause,
    // so when the phrase ends only the last bit is left to read. Used only when Fluid Speech Mini is the ears model.
    final class ChunkJob: @unchecked Sendable {
        var text: String?
        let done = DispatchSemaphore(value: 0)
    }
    struct Pending { var active = false; var jobs: [ChunkJob] = []; var base = 0; var tailSpeech = false }
    static let chunkPauseMs = 250.0                 // a pause this long inside a phrase cuts a chunk
    static let chunkMinSamples = Endpointer.rate * 3 / 2      // a chunk is at least 1.5 s of audio (its first 0.5 s may be the pre-roll)
    static let tailMinSamples = Endpointer.rate / 4           // a tail under 0.25 s is just the quiet at the end
    private let chunkQ = DispatchQueue(label: "misty.ears.chunk", qos: .userInitiated)
    private var chunkActive = false                  // vadQ: this phrase is being chunked
    private var chunkBase = 0                        // vadQ: samples of the open phrase already sent to a chunk read
    private var chunkJobs: [ChunkJob] = []           // vadQ
    private var chunkTriedQuiet = false              // vadQ: a chunk was considered for this quiet run
    /// Test hook: chunks are read with this instead of Fluid Speech Mini.
    var chunkReader: (@Sendable ([Float]) -> String?)?

    public init() { readQ = DispatchQueue(label: "misty.ears.read", qos: .userInitiated); shared = nil }

    /// A second stream (a class's system audio) with its own phrase tracking that reads with `other`'s model, on the
    /// same queue: one model in memory, and two streams never read at the same moment.
    public init(sharing other: Ears) { readQ = other.readQ; shared = other }

    public static var installed: Bool {
        FileManager.default.fileExists(atPath: modelURL.appendingPathComponent("manifest.json").path)
    }

    /// Load the model (once per launch; the first launch on a Mac also compiles it for the Neural Engine, a minute or two).
    public func warm() async -> Bool {
        EarsModel.warmMini()                       // Fluid Speech Mini loads in the background beside Phonon-2
        return await withCheckedContinuation { c in
            readQ.async { c.resume(returning: self.load()) }
        }
    }

    private func load() -> Bool {
        #if canImport(PhononCoreML)
        if model != nil { return true }
        guard Self.installed else { return false }
        var o = Transcriber.Options()
        // one encoder size, loaded up front: every phrase fits (the endpointer closes at 15 s) and nothing loads mid-turn.
        // Each size is its own ~110 MB copy on the Neural Engine, so all four sat at 660 MB; this one is ~115 MB.
        o.functions = [15]
        o.eagerFunctions = [15]
        do {
            let t = try Transcriber(bundle: Self.modelURL, options: o)
            _ = try? t.transcribe([Float](repeating: 0, count: Endpointer.rate / 2))   // first read pays the ANE warm-up
            model = t
            lock.lock(); loaded = true; lock.unlock()
            return true
        } catch {
            VoiceLog.write("ears load failed \(error)")
            lock.lock(); failed = true; lock.unlock()
            return false
        }
        #else
        return false
        #endif
    }

    private let lock = NSLock()
    private var loaded = false
    private var failed = false
    /// The load finished and did not work (the model is missing or Core ML refused it). Waiting longer won't help.
    public var loadFailed: Bool { lock.lock(); defer { lock.unlock() }; return failed }
    /// The model is in memory (never blocks: the first load can take minutes on a new Mac).
    public var ready: Bool { lock.lock(); defer { lock.unlock() }; return loaded }

    /// 16 kHz mono samples from the mic, any chunk size, any thread.
    public func feed(_ samples: [Float]) {
        vadQ.async { self.step(samples) }
    }

    /// He is mid-phrase right now.
    public var inSpeech: Bool { vadQ.sync { endpointer.inSpeech } }

    /// Endpointer state for the voice log: in speech, noise floor, last level.
    public func vadState() -> String {
        vadQ.sync { String(format: "speech=%d floor=%.4f level=%.4f", endpointer.inSpeech ? 1 : 0, endpointer.floor, endpointer.level) }
    }

    /// While Misty talks, leftover echo of her voice reaches the mic at ~0.002 RMS against ~0.02-0.09 for Isaac
    /// up close, so the bar for "speech" goes up and only him talking over her gets through.
    public func setSpeaking(_ on: Bool) {
        vadQ.async {
            self.endpointer.minLevel = on ? Endpointer.speakingMinLevel : Endpointer.quietMinLevel
            self.endpointer.overEcho = on
            self.speaking = on
            self.partialEvery = self.cadence()
            if on { self.endpointer.reset(); self.clearChunks(); self.session += 1; self.reading = false }   // a phrase open when she starts would fill with her echo
        }
    }

    /// Live mode on or off: lean partials (see `partialLean`). The final text of every phrase is unchanged.
    public func setLean(_ on: Bool) {
        vadQ.async { self.lean = on; self.partialEvery = self.cadence() }
    }

    private func cadence() -> Double { speaking ? Self.partialSpeaking : lean ? Self.partialLean : Self.partialListening }

    /// A class: the teacher is across the room, not at the mic, so quieter speech counts and pauses cut sooner.
    public func setClassMode(_ on: Bool) {
        vadQ.async {
            if on { self.endpointer.classTuned() } else { self.endpointer.closeTalk() }
            self.earlyOn = !on                         // F2: a class keeps its own end of phrase
            self.earlyTried = false
        }
    }

    /// End the phrase in flight now and read it (a class ended mid-sentence): its text, or nil if there was none.
    public func flush() async -> String? {
        let ended: ([Float], Pending)? = vadQ.sync {
            self.session += 1                         // a partial read still running must not land after this
            guard let w = self.endpointer.forceEnd() else { self.clearChunks(); return nil }
            return (w, self.takeChunks(w))
        }
        guard let (wave, pend) = ended else { return nil }
        return await withCheckedContinuation { (c: CheckedContinuation<String?, Never>) in
            readQ.async {
                let t = pend.active ? self.assemble(wave, pend) : self.transcribe(wave, final: true)
                c.resume(returning: t.isEmpty ? nil : t)
            }
        }
    }

    /// Read a whole recording in one go (dictation): 16 kHz mono samples in, text out. Queues behind any read in flight,
    /// so live mode and dictation share the one loaded model instead of each loading their own.
    public func readWhole(_ wave: [Float]) async -> String {
        await withCheckedContinuation { (c: CheckedContinuation<String, Never>) in
            readQ.async { c.resume(returning: self.transcribe(wave, final: true)) }
        }
    }

    /// V8: a quick read of the words so far while he still talks (dictation, tap-to-talk). Always Phonon-2, never the dev
    /// switch's model, and it queues like any other read.
    public func readPartial(_ wave: [Float]) async -> String {
        await withCheckedContinuation { (c: CheckedContinuation<String, Never>) in
            readQ.async { c.resume(returning: self.transcribe(wave, final: false)) }
        }
    }

    /// Forget the phrase in flight (new session, or Misty starts listening afresh).
    public func reset() {
        vadQ.async { self.endpointer.reset(); self.clearChunks(); self.session += 1; self.reading = false }
    }

    private func step(_ samples: [Float]) {
        for e in endpointer.feed(samples) {
            switch e {
            case .started:
                lastPartial = Date().timeIntervalSince1970
                sameText = ("", 0)
                emptyReads = 0
                startChunks()
            case .ended(let phrase):
                Self.dump(phrase)
                VoiceLog.write(String(format: "phrase %.1fs peak=%.4f", Double(phrase.count) / 16000, Self.peakLevel(phrase)))
                read(phrase, final: true, pending: takeChunks(phrase))
            }
        }
        let now = Date().timeIntervalSince1970
        if endpointer.inSpeech, !reading, now - lastPartial >= partialEvery {
            lastPartial = now
            let cur = endpointer.current
            read(lean && cur.count > Self.leanWindowSamples ? Array(cur.suffix(Self.leanWindowSamples)) : cur, final: false)
        }
        maybeChunk()
        maybeEarlyEnd()
    }

    private func startChunks() {
        chunkJobs = []; chunkBase = 0; chunkTriedQuiet = false
        chunkActive = chunkReader != nil
            || (testReader == nil && EarsModel.current == .fluidMini && EarsModel.fluidMini.isInstalled
                && MiniWorker.available(home: EarsModel.fluidMini.miniHome))
    }

    private func clearChunks() { chunkJobs = []; chunkBase = 0; chunkActive = false; chunkTriedQuiet = false }

    /// Does this audio hold anything louder than the room (so a tail that is only the quiet at the end is not read)?
    private func speechIn(_ w: [Float]) -> Bool {
        let bar = max(endpointer.minLevel, endpointer.floor * endpointer.ratio)
        var i = 0
        while i + Endpointer.frame <= w.count {
            var e: Float = 0
            for x in w[i..<(i + Endpointer.frame)] { e += x * x }
            if (e / Float(Endpointer.frame)).squareRoot() > bar { return true }
            i += Endpointer.frame
        }
        return false
    }

    /// At a short pause inside a phrase, hand everything said since the last cut to Fluid Speech Mini in the background.
    private func maybeChunk() {
        guard chunkActive, endpointer.inSpeech else { return }
        let quiet = endpointer.quietMs
        if quiet == 0 { chunkTriedQuiet = false; return }
        guard !chunkTriedQuiet, quiet >= Self.chunkPauseMs else { return }
        chunkTriedQuiet = true
        let cur = endpointer.current
        guard cur.count - chunkBase >= Self.chunkMinSamples else { return }
        let wave = Array(cur[chunkBase...])
        guard speechIn(wave) else { return }
        chunkBase = cur.count
        let job = ChunkJob()
        chunkJobs.append(job)
        let reader = chunkReader
        chunkQ.async {
            job.text = reader != nil ? reader?(wave) : EarsModel.fluidMini.read(wave)
            job.done.signal()
        }
    }

    /// The phrase is over (vadQ): take its chunk jobs and start the next phrase clean.
    private func takeChunks(_ phrase: [Float]) -> Pending {
        defer { clearChunks() }
        guard chunkActive else { return Pending() }
        let base = min(chunkBase, phrase.count)
        return Pending(active: true, jobs: chunkJobs, base: base, tailSpeech: speechIn(Array(phrase[base...])))
    }

    /// The final text of a chunked phrase (readQ): the chunks already read plus the tail. Any chunk that failed means the
    /// whole phrase is read again in one go, so a missing piece never reaches her.
    private func assemble(_ phrase: [Float], _ p: Pending) -> String {
        var parts: [String] = []
        for j in p.jobs {
            _ = j.done.wait(timeout: .now() + 30)
            guard let t = j.text, !t.isEmpty else { return transcribe(phrase, final: true) }
            parts.append(t)
        }
        if p.base < phrase.count, p.tailSpeech || p.jobs.isEmpty {
            let tail = p.jobs.isEmpty ? phrase : Array(phrase[p.base...])
            if tail.count >= Self.tailMinSamples {
                let t = transcribe(tail, final: true)
                if !t.isEmpty { parts.append(t) }
            }
        }
        return parts.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// F2: at 350 ms of quiet, read the phrase once more, now. If the words are plainly finished it ends here instead of
    /// at 650 ms, with that read as the final. If not, nothing changes and the normal end still comes. A partial can be
    /// up to 0.8 s old, so its text is never trusted for this; the early read is of the audio up to now.
    private func maybeEarlyEnd() {
        guard earlyOn, endpointer.inSpeech else { earlyTried = false; return }
        let quiet = endpointer.quietMs
        if quiet == 0 { earlyTried = false; return }                 // he is talking: the next quiet run is a new one
        guard !earlyTried, !reading, quiet >= Endpointer.earlyEndMs, quiet < endpointer.endMs else { return }
        earlyTried = true
        reading = true                                               // no partial read starts beside it
        let wave = endpointer.current, s = session, t0 = Date()
        readQ.async {
            let text = self.transcribe(wave)
            self.vadQ.async {
                self.reading = false
                guard s == self.session, self.endpointer.inSpeech,
                      self.endpointer.quietMs >= Endpointer.earlyEndMs,        // he spoke again meanwhile: leave it open
                      VoiceIntent.looksFinished(text) else { return }
                let phraseNow = self.endpointer.current
                guard self.endpointer.forceEnd() != nil else { return }
                let pend = self.takeChunks(phraseNow)
                self.sameText = ("", 0)
                self.emptyReads = 0
                self.lock.lock(); self.earlyAtStore = Date(); self.lock.unlock()
                if self.testReader == nil {                                  // tests never write Isaac's real log
                    VoiceLog.write(String(format: "EARLY_END quiet_ms=%.0f read_ms=%d", Endpointer.earlyEndMs, Int(Date().timeIntervalSince(t0) * 1000)))
                }
                guard pend.active else { self.onFrame?(.final(text)); return }
                // the early read only decided the phrase was finished; the words she gets are Fluid Speech Mini's (chunks + tail)
                self.readQ.async {
                    let mini = self.assemble(phraseNow, pend)
                    self.vadQ.async { self.onFrame?(.final(mini.isEmpty ? text : mini)) }
                }
            }
        }
    }

    /// Test hook: wait until everything queued has run.
    func _drainForTests() { vadQ.sync {}; readQ.sync {}; vadQ.sync {} }

    private func read(_ wave: [Float], final: Bool, pending: Pending = Pending()) {
        let s = session
        if !final { reading = true }
        readQ.async {
            let text = final && pending.active ? self.assemble(wave, pending) : self.transcribe(wave, final: final)
            self.vadQ.async {
                if !final { self.reading = false }
                guard s == self.session else { return }
                if final {
                    // a phrase with no words (a cough, a door) closes silently
                    if text.contains(where: \.isLetter) { self.onFrame?(.final(text)) }
                } else if !text.contains(where: \.isLetter) {
                    self.emptyReads += 1
                    if self.emptyReads >= Self.noiseReads { self.endpointer.dropAsNoise(); self.clearChunks(); self.emptyReads = 0 }
                } else {
                    self.emptyReads = 0
                    self.onFrame?(.partial(text))
                    self.settle(text)
                }
            }
        }
    }

    /// The words stopped changing while the room stayed too loud to call it quiet: close the phrase.
    private func settle(_ text: String) {
        sameText = text == sameText.0 ? (text, sameText.1 + 1) : (text, 1)
        guard sameText.1 >= Self.settledReads, let phrase = endpointer.forceEnd() else { return }
        sameText = ("", 0)
        Self.dump(phrase)
        read(phrase, final: true)
    }

    /// Loudest 20 ms frame of a phrase (RMS), for the voice log: tells Isaac up close from the room and from echo.
    static func peakLevel(_ w: [Float]) -> Float {
        stride(from: 0, to: max(0, w.count - 320), by: 320).map { i in
            (w[i..<(i + 320)].reduce(0) { $0 + $1 * $1 } / 320).squareRoot()
        }.max() ?? 0
    }

    /// Debug only: with MISTY_EARS_DUMP=<folder> each finished phrase is saved there as a wav. Off by default,
    /// so Isaac's voice is never written to disk in normal use.
    static func dump(_ wave: [Float]) {
        guard let dir = ProcessInfo.processInfo.environment["MISTY_EARS_DUMP"] else { return }
        var d = Data()
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        let n = UInt32(wave.count * 2)
        d.append(contentsOf: Array("RIFF".utf8)); u32(36 + n); d.append(contentsOf: Array("WAVEfmt ".utf8))
        u32(16); u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16)
        d.append(contentsOf: Array("data".utf8)); u32(n)
        for x in wave { u16(UInt16(bitPattern: Int16(max(-1, min(1, x)) * 32767))) }
        try? d.write(to: URL(fileURLWithPath: dir).appendingPathComponent("phrase-\(Int(Date().timeIntervalSince1970 * 1000)).wav"))
    }

    private func transcribe(_ wave: [Float], final: Bool = false) -> String {
        if let r = testReader { return r(wave) }
        // every finished phrase (talk, live, class, dictation, flush) is read by the chosen model, Fluid Speech Mini by default.
        // Live partials and the early-end read stay on Phonon-2: they must come back in a fraction of a second, Mini takes ~1.5 s.
        if final, EarsModel.current != .phonon2, wave.count >= Endpointer.rate / 5 {
            let m = EarsModel.current, t0 = Date()
            if let text = m.read(wave) {
                VoiceLog.write(String(format: "ears read model=%@ %.1fs%@", m.rawValue, Date().timeIntervalSince(t0), text.isEmpty ? " no words" : ""))
                return text
            }
            VoiceLog.write("ears read model=\(m.rawValue) failed, using phonon2")
        }
        #if canImport(PhononCoreML)
        guard let model = model ?? shared?.model, wave.count >= Endpointer.rate / 5 else { return "" }
        do { return try model.transcribe(wave).text.trimmingCharacters(in: .whitespacesAndNewlines) }
        catch { VoiceLog.write("ears read failed \(error)"); return "" }
        #else
        return ""
        #endif
    }
}
