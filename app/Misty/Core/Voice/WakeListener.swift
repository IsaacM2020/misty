import Foundation
import Observation

/// Owns the mic while EDITH is idle and listens for her name. When the gate closes (a session, her own voice, a class, a call,
/// dictation, the setting off) the mic is let go; when it opens again the mic reopens with a fresh spotter. Nothing is
/// recorded: audio goes through the spotter and is dropped, except for the moment right after a detection (see `takeTail`).
///
/// Threading: audio arrives on the engine's thread and goes straight into `WakeStream` (a lock around the spotter and the
/// tail). Everything else is on the main actor.
@MainActor
@Observable
public final class WakeListener {
    public private(set) var settings: WakeSettings
    public private(set) var listening = false
    /// The live gate, asked about four times a second and at every detection. AppState fills it from the voice, class, dictation and call state.
    @ObservationIgnored public var gate: @MainActor () -> WakeGate = { WakeGate() }
    /// A detection that passed the gate. Returns true when it started the session (so the tail will be taken).
    @ObservationIgnored public var onWake: @MainActor () async -> Bool = { false }

    @ObservationIgnored public private(set) var wakes = 0
    /// Why it is not listening right now (for the log and a test).
    @ObservationIgnored public private(set) var lastBlocker: String? = "not started"

    @ObservationIgnored private let home: String
    /// Whether the library, the word's tokens and the model files are all here. Tests replace it.
    /// Only let the spotter hear audio that has sound in it (see `WakeActivity`). Tests turn it off to feed tiny made-up samples.
    @ObservationIgnored var gateAudio = true
    @ObservationIgnored var modelReady: (WakeSettings) -> Bool = { _ in false }
    @ObservationIgnored private let make: (String, WakeSettings) -> WakeSpotter?
    @ObservationIgnored private let source: WakeMic
    @ObservationIgnored private var stream: WakeStream?
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var handoffAt: Date?    // set at a detection; the mic stays open until `takeTail` or this long
    @ObservationIgnored private var lastWake = Date.distantPast
    static let handoffMax: TimeInterval = 1.5
    static let cooldown: TimeInterval = 2.0       // a second detection this soon after one is ignored
    static let tick: Duration = .milliseconds(250)

    public init(home: String, settings: WakeSettings = .standard, mic: WakeMic = RealWakeMic(),
                make: @escaping (String, WakeSettings) -> WakeSpotter? = { WakeSpotterFactory.make(home: $0, settings: $1) }) {
        self.home = home; self.settings = settings; self.source = mic; self.make = make
        self.modelReady = { s in WakeModel.compiledIn && s.wordSupported && WakeModel.installed(home: home) }
    }

    // MARK: control

    /// Apply a setting from the engine or the menu. A changed word or sensitivity rebuilds the spotter at the next open.
    public func apply(_ s: WakeSettings) {
        let rebuild = s.word != settings.word || s.sensitivity != settings.sensitivity
        settings = s
        if rebuild, listening { closeMic("setting changed") }
        evaluate()
    }

    public func start() {
        guard task == nil else { return }
        task = Task { [weak self] in
            while !Task.isCancelled {
                self?.evaluate()
                try? await Task.sleep(for: Self.tick)
            }
        }
    }

    public func stop() {
        task?.cancel(); task = nil
        closeMic("stopped")
        lastBlocker = "not started"
    }

    /// The gate as the listener sees it, with what only it knows (the model files) filled in.
    func currentGate() -> WakeGate {
        var g = gate()
        g.enabled = g.enabled && settings.enabled
        g.modelReady = g.modelReady && modelReady(settings)
        return g
    }

    /// Open or close the mic to match the gate. Called every tick; cheap when nothing changes.
    func evaluate() {
        let g = currentGate()
        lastBlocker = g.blocker
        if g.open {
            if !listening { openMic(); return }
            if let h = handoffAt, Date().timeIntervalSince(h) > Self.handoffMax {   // the session never took hold: listen for her name again
                handoffAt = nil
                stream?.clearTail()
            }
            return
        }
        guard listening else { return }
        // a detection just started a session: the mic stays open until the session has taken the words that followed "Edith"
        if let h = handoffAt, stream?.tailPending == true, Date().timeIntervalSince(h) < Self.handoffMax { return }
        closeMic(g.blocker ?? "closed")
    }

    // MARK: the mic

    private func openMic() {
        guard let spotter = make(home, settings) else { lastBlocker = "no spotter"; return }
        let s = WakeStream(spotter: spotter, gated: gateAudio) { [weak self] in
            Task { @MainActor in await self?.detected() }
        }
        stream = s
        do {
            try source.start { samples in s.feed(samples) }
            listening = true
            VoiceLog.write("WAKE listening for \"\(settings.word)\" sensitivity=\(settings.sensitivity)")
        } catch {
            stream = nil
            lastBlocker = "mic would not open"
            VoiceLog.write("WAKE mic would not open: \(error)")
        }
    }

    private func closeMic(_ why: String) {
        guard listening else { return }
        source.stop()
        stream?.close()
        stream = nil
        handoffAt = nil
        listening = false
        VoiceLog.write("WAKE mic closed (\(why))")
    }

    // MARK: a detection

    private func detected() async {
        guard listening, handoffAt == nil, Date().timeIntervalSince(lastWake) >= Self.cooldown else { return }
        let g = currentGate()
        guard g.open else { VoiceLog.write("WAKE ignored (\(g.blocker ?? "?"))"); stream?.clearTail(); return }
        lastWake = Date()
        handoffAt = Date()
        wakes += 1
        VoiceLog.write("WAKE heard")
        let started = await onWake()
        if !started { handoffAt = nil; stream?.clearTail() }
    }

    /// What was said after the keyword, up to now. `VoiceVM` takes it when its recorder opens, so the rest of "Edith, what's
    /// next today?" is not lost in the hand-off. Empty when nothing is pending.
    public func takeTail() -> [Float] { stream?.takeTail() ?? [] }

    // MARK: test hooks

    /// A detection as if the spotter had fired (tests drive the whole path without a model or a mic).
    func testDetect() async { stream?.testFire(); await detected() }
}

/// The audio a detection is made from. The real one is `AudioIO`; a test supplies its own.
public protocol WakeMic: AnyObject {
    func start(_ onSamples: @escaping @Sendable ([Float]) -> Void) throws
    func stop()
}

/// The app's own mic path: a plain engine (no voice processing, so no other app's volume is touched), reopened by itself when the
/// audio route changes.
public final class RealWakeMic: WakeMic {
    private let io = AudioIO(voiceProcessing: false)
    public init() { io.restartOnRouteChange = true }
    public func start(_ onSamples: @escaping @Sendable ([Float]) -> Void) throws { try io.start(onSamples: onSamples) }
    public func stop() { io.stop() }
}

/// Runs `WakeTurn` on the recorder's audio thread and reports its one decision.
final class WakeTurnWatcher: @unchecked Sendable {
    private let lock = NSLock()
    private var turn = WakeTurn()
    private var finished = false
    private let onDecision: @Sendable (WakeTurn.Decision) -> Void

    init(onDecision: @escaping @Sendable (WakeTurn.Decision) -> Void) { self.onDecision = onDecision }

    func feed(_ samples: [Float]) {
        lock.lock()
        var d = WakeTurn.Decision.keep
        if !finished { d = turn.feed(samples); if d != .keep { finished = true } }
        lock.unlock()
        if d != .keep { onDecision(d) }
    }
}

/// Joins the wake listener to the voice loop: the listener's gate is the voice's gate, a detection runs `VoiceVM.wake`, and the
/// voice takes the listener's tail when its recorder opens. App code calls this once; tests call it with a fake mic.
@MainActor
public enum WakeWiring {
    public static func connect(listener: WakeListener, voice: VoiceVM, context: @escaping @MainActor () -> WakeGate) {
        listener.gate = { [weak voice] in
            guard let voice else { var g = WakeGate(); g.enabled = false; return g }
            return voice.wakeGate(context())
        }
        listener.onWake = { [weak voice] in await voice?.wake(base: context()) ?? false }
        voice.wakeTail = { [weak listener] in listener?.takeTail() ?? [] }
    }
}
