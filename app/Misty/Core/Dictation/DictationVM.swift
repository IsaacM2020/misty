import AppKit
import Observation
import SwiftUI

/// Push-to-talk dictation inside Misty (Ripple 2, moved in). Hold Right Option or Right Control, talk, let go: the words
/// land at the cursor. Her ears model (the same one live mode uses) reads the recording on this Mac, so nothing is sent
/// anywhere. Tap `/` while holding to lock it on hands-free.
///
/// Off until Isaac turns it on in Settings: while Ripple 2 is still running both would react to the same key.
@MainActor
@Observable
public final class DictationVM {
    public enum Phase: Equatable { case idle, recording(locked: Bool), reading, done(String) }

    public private(set) var phase: Phase = .idle
    /// Newest first. The pill's bars read outward from the centre, so a syllable ripples out (Ripple 2's meter).
    public var history: [Float] { meter.history }
    @ObservationIgnored private var meterRaw = LevelSmoother()
    private var meter = LevelSmoother()
    /// V8: his words so far, read live while he talks (the final read after he lets go is what gets pasted).
    public private(set) var words = ""
    public private(set) var problem: String?
    public private(set) var keyOK = true
    public private(set) var enabled: Bool

    static let defaultsKey = "dictationOn"
    static let minSeconds = 0.35                       // anything shorter was a bump of the key, not speech

    @ObservationIgnored private let ears: Ears
    @ObservationIgnored private let key = DictationKey()
    @ObservationIgnored private let recorder = DictationRecorder()
    /// V10: the mouse is over the pill: the card opens on everything he has said so far (scroll to read back).
    public internal(set) var hovering = false
    public internal(set) var scroll: CGFloat = 0
    @ObservationIgnored private lazy var pill = PillWindow(
        onHover: { [weak self] on in
            guard let self else { return }
            self.hovering = on; self.scroll = 0; self.recorder.wantAll = on
        },
        onScroll: { [weak self] d in if let self { self.scroll = max(0, self.scroll + d) } }
    ) { AnyView(DictationPillView(vm: self)) }
    @ObservationIgnored private var startedAt = Date()
    @ObservationIgnored private var hideTask: Task<Void, Never>?
    @ObservationIgnored private let live = LiveReader()

    public init(ears: Ears) {
        self.ears = ears
        enabled = UserDefaults.standard.bool(forKey: Self.defaultsKey)
        key.onPress = { [weak self] in self?.press() }
        key.onRelease = { [weak self] in Task { await self?.finish() } }
        key.onLock = { [weak self] on in
            guard let self else { return }
            if on {
                if !self.recorder.running { self.press() }
                if self.recorder.running { self.phase = .recording(locked: true) }
            } else { Task { await self.finish() } }
        }
        key.onCancel = { [weak self] in self?.cancel() }
        recorder.onLevel = { [weak self] level in
            Task { @MainActor in self?.push(level) }
        }
    }

    /// Words for the pill.
    public var word: String {
        if let problem { return problem }
        switch phase {
        case .idle: return ""
        case .recording(let locked): return locked ? "Locked" : "Listening"
        case .reading: return "Reading"
        case .done(let how): return how
        }
    }

    /// Test and screenshot hook: put the pill in a state without a mic or a key.
    func testShow(phase: Phase, problem: String? = nil, history: [Float]? = nil, words: String = "", hovering: Bool = false) {
        self.words = words
        self.hovering = hovering
        self.phase = phase; self.problem = problem
        if let history { meter = LevelSmoother(test: history) }
    }

    public var recording: Bool { if case .recording = phase { return true } else { return false } }

    // MARK: on and off

    public func setEnabled(_ on: Bool) {
        enabled = on
        UserDefaults.standard.set(on, forKey: Self.defaultsKey)
        apply()
    }

    /// Start or stop the key watcher to match the switch. Called at launch and when the switch moves.
    public func apply() {
        if enabled {
            keyOK = key.start()
            if !keyOK { VoiceLog.write("dictation: key tap refused (Accessibility?)") }
        } else {
            key.stop()
            if recorder.running { _ = recorder.stop() }
            phase = .idle
            pill.hide()
            keyOK = true
        }
    }

    /// Ripple 2 is the app this replaces. While it runs, the same key starts both.
    public var rippleRunning: Bool {
        NSWorkspace.shared.runningApplications.contains { $0.bundleIdentifier?.lowercased().contains("ripple") == true || $0.localizedName == "Ripple2" }
    }

    public func quitRipple() {
        for a in NSWorkspace.shared.runningApplications where a.bundleIdentifier?.lowercased().contains("ripple") == true || a.localizedName == "Ripple2" {
            a.terminate()
        }
    }

    // MARK: one dictation

    private func press() {
        guard enabled else { return }
        switch phase { case .idle, .done: break; default: return }
        hideTask?.cancel()
        problem = nil
        guard AudioIO.micAllowed else { return flash(problem: "Mic is off: Settings > Permissions") }
        // Still loading is fine: you can talk already and the reading waits its turn behind the load.
        guard !ears.loadFailed else { return flash(problem: "Ears didn't load") }
        do { try recorder.start() } catch {
            VoiceLog.write("dictation: mic start failed \(error)")
            return flash(problem: "Couldn't open the mic")
        }
        meterRaw.reset(); meter = meterRaw
        words = ""
        hovering = false; scroll = 0; recorder.wantAll = false     // a fresh pill starts closed; moving onto it opens it
        startedAt = Date()
        phase = .recording(locked: false)
        let rec = recorder
        live.start(ears: ears, snapshot: { rec.snapshot() }) { [weak self] t in
            guard let self, self.recording else { return }
            self.words = t
        }
        Sound.shared.play(.down)
        pill.show()
    }

    private func cancel() {
        guard recorder.running else { return }
        live.stop()
        _ = recorder.stop()
        phase = .idle
        words = ""
        pill.hide()
    }

    private func finish() async {
        guard recorder.running else { return }
        live.stop()
        await read(recorder.stop())
    }

    /// The test without a mic or a key: MISTY_DICTATE_WAV=<sound file> is read, cleaned and pasted `after` seconds
    /// from now, into whatever has the cursor. For checking the whole path on a Mac where the room must stay quiet.
    public func testFromFile(_ path: String, after: Double) {
        Task {
            try? await Task.sleep(for: .seconds(after))
            while !ears.ready, !ears.loadFailed { try? await Task.sleep(for: .milliseconds(250)) }
            guard let wave = try? AudioIO.fileSamples(path) else { return VoiceLog.write("dictation test: can't read \(path)") }
            // MISTY_DICTATE_TARGET=window gives the paste a text window of its own to land in (macOS won't let a background
            // app raise another app), and logs what ended up inside it.
            var box: NSTextView?
            if ProcessInfo.processInfo.environment["MISTY_DICTATE_TARGET"] == "window" {
                let tv = NSTextView(frame: NSRect(x: 0, y: 0, width: 420, height: 140))
                let w = NSWindow(contentRect: tv.frame, styleMask: [.titled], backing: .buffered, defer: false)
                w.title = "Dictation test"
                w.contentView = tv
                w.isReleasedWhenClosed = false
                NSApp.activate()
                w.makeKeyAndOrderFront(nil)
                w.makeFirstResponder(tv)
                box = tv
                try? await Task.sleep(for: .seconds(1))
            }
            pill.show()
            await read(wave)
            if let box { try? await Task.sleep(for: .milliseconds(600)); VoiceLog.write("dictation test window now holds: \(box.string)") }
        }
    }

    /// The test without the key: record the real mic for `seconds` starting `after` seconds from now, then read it.
    /// With the voice pill showing and the speaker playing a sentence this proves the mic path (levels, silence) alone.
    public func testFromMic(seconds: Double, after: Double) {
        Task {
            try? await Task.sleep(for: .seconds(after))
            while !ears.ready, !ears.loadFailed { try? await Task.sleep(for: .milliseconds(250)) }
            press()
            try? await Task.sleep(for: .seconds(seconds))
            await finish()
        }
    }

    private func read(_ wave: [Float]) async {
        let seconds = Double(wave.count) / 16000
        guard seconds >= Self.minSeconds else {
            VoiceLog.write(String(format: "dictation ignored: %.2fs of audio, ", seconds) + recorder.report)
            phase = .idle; pill.hide(); return
        }
        phase = .reading
        let t0 = ContinuousClock.now
        // a recording of silence would come back from Whisper as "Thank you.": don't ask
        let peak = wave.reduce(Float(0)) { max($0, abs($1)) }
        guard peak >= 0.01 else {
            VoiceLog.write(String(format: "dictation: mic gave silence (%.1fs, peak %.4f)", seconds, peak))
            return flash(problem: "Mic gave silence")
        }
        let raw = await transcribe(wave)
        let text = DictationText.clean(raw)
        DictationSamples.keepIfOn(wave, raw: raw, cleaned: text)
        VoiceLog.write(String(format: "dictation %.1fs audio, peak %.3f, read in %d ms, %d chars", seconds, peak,
                              Int((ContinuousClock.now - t0) / .milliseconds(1)), text.count))
        guard text.contains(where: \.isLetter) else {
            return flash(problem: "Heard nothing")
        }
        if ProcessInfo.processInfo.environment["MISTY_DICTATE_DRY"] != nil {      // tests: say what was heard, paste nothing
            VoiceLog.write("dictation dry run heard: \(text)")
            phase = .idle; pill.hide(); return
        }
        switch DictationDelivery.deliver(text) {
        case .pasted: words = ""; phase = .done("Pasted"); VoiceLog.write("dictation pasted")
        case .copied(let why): words = ""; phase = .done("Copied, \(why)"); VoiceLog.write("dictation copied only: \(why)")
        }
        Sound.shared.play(.done)
        hideLater(after: 1.3)
    }

    /// Phonon-2 on the Neural Engine, in this app: nothing is sent anywhere. It loads at launch (a couple of seconds; only
    /// the very first launch on a Mac compiles for a minute), and a recording made before it is ready is read right after.
    private func transcribe(_ wave: [Float]) async -> String {
        await ears.readWhole(wave)
    }

    private func flash(problem why: String) {
        live.stop()
        words = ""
        problem = why
        phase = .idle
        pill.show()
        hideLater(after: 2.2)
    }

    private func hideLater(after s: Double) {
        hideTask?.cancel()
        hideTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(s))
            guard !Task.isCancelled, let self else { return }
            if !self.recording && self.phase != .reading { self.problem = nil; self.words = ""; self.phase = .idle; self.pill.hide() }
        }
    }

    /// Loudness to the meter (see `LevelSmoother`): fast up, slower down, so it moves with the voice and doesn't flicker.
    private func push(_ rms: Float) {
        guard recording else { return }
        meterRaw.push(rms)
        meter = meterRaw
    }
}

/// V8: what the dictation card shows. The same light-glass card as live mode's pill, morphing between these.
/// Once his words appear the card is one steady width, so it never jitters as words come and go.
enum DictationCard: Equatable {
    case listening(locked: Bool, words: String)   // the real meter, then his words rising in as he says them
    case reading(words: String)                   // the meter becomes three dots; his words stay put
    case done(String)                             // "Pasted", "Copied, ..." for a moment
    case problem(String)                          // "Mic gave silence", "Heard nothing"

    static func from(phase: DictationVM.Phase, problem: String?, words: String = "") -> DictationCard {
        if let p = problem, !p.isEmpty { return .problem(p) }
        switch phase {
        case .recording(let locked): return .listening(locked: locked, words: words)
        case .reading, .idle: return .reading(words: words)
        case .done(let how): return .done(how)
        }
    }

    var words: String {
        switch self {
        case .listening(_, let w), .reading(let w): return w.trimmingCharacters(in: .whitespacesAndNewlines)
        default: return ""
        }
    }

    var isListening: Bool { if case .listening = self { return true } else { return false } }
    var isLocked: Bool { if case .listening(let l, _) = self { return l } else { return false } }

    /// Same key = same shape: only the inside changes, the card does not resize.
    var key: String {
        switch self {
        case .listening(let l, let w): return (w.isEmpty ? "meter" : "words") + (l ? "-locked" : "")
        case .reading(let w): return w.isEmpty ? "dots" : "words"
        case .done: return "done"
        case .problem: return "problem"
        }
    }

    static let iconWidth: CGFloat = LevelMeter.width
    static let lockWidth: CGFloat = 10
    static let wordsWidth: CGFloat = 300
    /// V10: the open card shows up to this many lines at once; past that it follows the newest words (scroll to go back).
    static let openLines = 14

    /// Hovering a card that has his words opens it on all of them.
    func opens(hovering: Bool) -> Bool { hovering && !words.isEmpty }

    /// The open card's text column, and the box the words sit in (as tall as they are, up to `openLines`).
    static func openTextWidth(screenWidth: CGFloat, locked: Bool) -> CGFloat {
        PillLayout.width(screenWidth: screenWidth) - 12 - max(LevelMeter.width, 28) - 8 - 14 - (locked ? 8 + lockWidth : 0)
    }
    static func openTextHeight(_ text: String, width: CGFloat) -> CGFloat {
        min(max(PillLayout.measure(text, width: width).height, PillLayout.lineHeight), CGFloat(openLines) * PillLayout.lineHeight)
    }

    func size(screenWidth: CGFloat, hovering: Bool = false) -> CGSize {
        let cap = PillLayout.width(screenWidth: screenWidth)
        if opens(hovering: hovering) {
            let h = Self.openTextHeight(words, width: Self.openTextWidth(screenWidth: screenWidth, locked: isLocked))
            return CGSize(width: cap, height: max(PillLayout.oneLine, h + 16))
        }
        switch self {
        case .listening(let locked, let w):
            if !w.trimmingCharacters(in: .whitespaces).isEmpty {
                return CGSize(width: min(Self.wordsWidth + (locked ? 8 + Self.lockWidth : 0), cap), height: PillLayout.oneLine)
            }
            return CGSize(width: 13 + Self.iconWidth + (locked ? 8 + Self.lockWidth : 0) + 13, height: PillLayout.oneLine)
        case .reading(let w):
            if !w.trimmingCharacters(in: .whitespaces).isEmpty { return CGSize(width: min(Self.wordsWidth, cap), height: PillLayout.oneLine) }
            return CGSize(width: 54, height: PillLayout.oneLine)
        case .done(let t), .problem(let t):
            return CGSize(width: min(12 + 8 + 8 + PillLayout.measure(t).width + 14, cap), height: PillLayout.oneLine)
        }
    }
}

/// The dictation pill (V8): live mode's light-glass card. A small meter that moves with his real voice, his words rising
/// in one by one as he talks, three soft dots while the last read runs, then what happened, then it fades.
struct DictationPillView: View {
    let vm: DictationVM
    /// Test and screenshot hook: a flat fill instead of the live blur.
    var flat = false
    @Environment(\.colorScheme) private var scheme
    @State private var reduceMotion = Motion.reduce

    private var screenWidth: CGFloat { (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame.width ?? 800 }

    var body: some View {
        let c = DictationCard.from(phase: vm.phase, problem: vm.problem, words: vm.words)
        let open = c.opens(hovering: vm.hovering)
        let card = c.size(screenWidth: screenWidth, hovering: vm.hovering)
        GlassCard(card: card, flat: flat) { content(c, open: open) }
            .animation(Motion.or(Motion.morph), value: c.key)
            .animation(Motion.or(Motion.morph), value: open)
    }

    @ViewBuilder
    private func content(_ c: DictationCard, open: Bool) -> some View {
        switch c {
        case .listening where open, .reading where open:
            // V10: open on everything said so far, wrapped; the card morphs to fit, the newest words stay in view.
            let tw = DictationCard.openTextWidth(screenWidth: screenWidth, locked: c.isLocked)
            let box = DictationCard.openTextHeight(c.words, width: tw)
            HStack(alignment: .top, spacing: 8) {
                ZStack {
                    if c.isListening { LevelMeter(history: vm.history).transition(.arrive) }
                    else { ThreeDots(reduceMotion: reduceMotion).transition(.arrive) }
                }
                .frame(width: max(LevelMeter.width, 28), height: PillLayout.lineHeight)
                Text(c.words).font(.system(size: PillLayout.fontSize)).foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: tw, alignment: .leading)
                    .modifier(FollowBottom(box: box, scroll: vm.scroll))
                    .frame(width: tw, height: box, alignment: .topLeading).clipped()
                if c.isLocked {
                    Image(systemName: "lock.fill").font(.system(size: 9, weight: .semibold)).foregroundStyle(Theme.lime)
                        .frame(width: DictationCard.lockWidth, height: PillLayout.lineHeight)
                }
            }
            .padding(.leading, 12).padding(.trailing, 14).padding(.vertical, 8)
            .transition(.arrive)
        case .listening, .reading:
            HStack(spacing: 8) {
                ZStack {
                    if c.isListening { LevelMeter(history: vm.history).transition(.arrive) }
                    else { ThreeDots(reduceMotion: reduceMotion).transition(.arrive) }
                }
                .frame(width: max(LevelMeter.width, 28))
                if !c.words.isEmpty { LiveWords(text: c.words).transition(.arrive) }
                if c.isLocked {
                    Image(systemName: "lock.fill").font(.system(size: 9, weight: .semibold)).foregroundStyle(Theme.lime)
                        .frame(width: DictationCard.lockWidth).transition(.arrive)
                }
            }
            .padding(.horizontal, c.words.isEmpty ? 0 : 12)
        case .done(let t), .problem(let t):
            HStack(spacing: 8) {
                Image(systemName: c.key == "problem" ? "exclamationmark" : "checkmark")
                    .font(.system(size: 9, weight: .bold)).foregroundStyle(c.key == "problem" ? Theme.urgent : Theme.lime)
                    .frame(width: 8)
                Text(t).font(.system(size: PillLayout.fontSize)).foregroundStyle(.primary).lineLimit(1).truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.leading, 12).padding(.trailing, 14).transition(.arrive)
        }
    }
}
