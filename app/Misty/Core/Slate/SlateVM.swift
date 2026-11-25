import Foundation
import Observation

/// What the Slate shows. The engine sends a checked slate (`slate.show`); this hands it to the page (`window.slate`),
/// paces the lines when there is no voice, and fades a quick slate out a few seconds after the last word.
/// The page lives in a web view that exists only while the window is open (the panel makes it, and frees it on close).
@MainActor
@Observable
public final class SlateVM {
    public private(set) var open = false
    public private(set) var title = ""
    public private(set) var says: [String] = []
    public private(set) var kind = "quick"
    /// Seconds a quick slate stays after its last word. A test sets it small.
    public var lingerSeconds: Double = 4
    public var fadeSeconds: Double = 1.6
    /// Set by the panel: closes the window once the slate has faded.
    @ObservationIgnored public var onClose: (() -> Void)?

    /// Set by the app: a pill on the read-back was pressed; this sends the word he would have said ("ok", "I want to change something").
    @ObservationIgnored public var onPill: ((String) -> Void)?
    public private(set) var asking = false                 // a protocol is waiting for his word
    @ObservationIgnored private var hovering = false
    @ObservationIgnored private var fadeDue = false        // the last word is written and a fade is owed
    @ObservationIgnored private var readbackTask: Task<Void, Never>?

    @ObservationIgnored private var evaluate: (@MainActor (String) -> Void)?
    @ObservationIgnored private var queued: [String] = []
    @ObservationIgnored private var lengths: [Int] = []
    @ObservationIgnored private var player: Task<Void, Never>?
    @ObservationIgnored private var fader: Task<Void, Never>?
    @ObservationIgnored private var sent: [String] = []        // every script handed to the page (for tests)
    public var scripts: [String] { sent }

    public init() {}

    /// How long a line's pen wipe takes with no voice: 55 ms a character, at least 700 ms.
    public static func wipeMs(chars: Int) -> Int { max(700, 55 * chars) }
    /// Room for the red mark to be drawn after the last wipe.
    public static let markMs = 900

    /// Milliseconds of audio per spoken character, learned from the sentences already said (starts at a typical value for her voice).
    @ObservationIgnored public private(set) var msPerChar: Double = 62
    /// Per line: when its estimate was handed to the page (so a sync check can be logged).
    @ObservationIgnored private var startedAt: [Int: Double] = [:]
    @ObservationIgnored private var starter: Task<Void, Never>?

    /// The wipe length to start with, before the exact audio length is known: characters x learned pace, at least 500 ms.
    public static func estimateMs(chars: Int, msPerChar: Double) -> Int { max(500, Int((Double(chars) * msPerChar).rounded())) }
    /// The pace after hearing one more sentence: half old, half new, kept inside a sane range.
    public static func learnedPace(old: Double, chars: Int, audioSeconds: Double) -> Double {
        guard chars >= 8, audioSeconds > 0 else { return old }
        return min(120, max(30, 0.5 * old + 0.5 * audioSeconds * 1000 / Double(chars)))
    }
    /// What the page is told so the wipe ends exactly when the audio does.
    public static func retimeMs(endsIn: Double) -> Int { max(0, Int((endsIn * 1000).rounded())) }

    /// What `VoiceVM.narrate` calls as the speaker reports each line's audio.
    public var timing: SlateTiming { timing(offset: 0) }
    /// The same, for lines added to a slate already on screen: the narrator counts from 0, the page from `offset`.
    public func timing(offset: Int) -> SlateTiming {
        SlateTiming(start: { [weak self] i, startsIn in self?.lineStarts(i + offset, startsIn: startsIn) },
                    end: { [weak self] i, endsIn, secs in self?.lineEnds(i + offset, endsIn: endsIn, audioSeconds: secs) },
                    silent: { [weak self] i in self?.reveal(i + offset) },
                    stopped: { [weak self] in self?.stopLine() })
    }

    /// Line `i`'s audio begins in `startsIn` s: start the wipe on an estimate (the exact end arrives a moment later).
    public func lineStarts(_ i: Int, startsIn: Double) {
        guard i < says.count else { return }
        let ms = Self.estimateMs(chars: says[i].count, msPerChar: msPerChar)
        starter?.cancel()
        guard startsIn > 0.12 else { return reveal(i, ms: ms) }          // queued behind other audio: wait for it
        starter = Task { [weak self] in
            try? await Task.sleep(for: .seconds(startsIn))
            guard !Task.isCancelled else { return }
            self?.reveal(i, ms: ms)
        }
    }

    /// Line `i` is fully generated, so its audio ends exactly `endsIn` s from now: the page stretches or squeezes the rest of
    /// the wipe to land there, and the pace estimate learns from the real length.
    public func lineEnds(_ i: Int, endsIn: Double, audioSeconds: Double) {
        guard i < says.count else { return }
        msPerChar = Self.learnedPace(old: msPerChar, chars: says[i].count, audioSeconds: audioSeconds)
        let ms = Self.retimeMs(endsIn: endsIn)
        run("window.slate.retime(\(i), \(ms))")
        NSLog("SLATE sync line=%d say_chars=%d audio_ms=%d ends_in_ms=%d pace=%.1f", i, says[i].count, Int(audioSeconds * 1000), ms, msPerChar)
        if i == lengths.count - 1 { scheduleFade(after: endsIn + Double(Self.markMs) / 1000) }
    }

    /// A barge-in or a tap: stop the clocks and finish the line being inked at once, so no word is left half written.
    public func stopLine() {
        starter?.cancel(); player?.cancel(); fader?.cancel()
        run("window.slate.finishLine()")
    }

    /// The page finished loading: run whatever was waiting.
    public func pageReady(_ run: @escaping @MainActor (String) -> Void) {
        evaluate = run
        for js in queued { run(js) }
        queued = []
    }
    /// The window closed and its web view is gone.
    public func pageGone() {
        evaluate = nil
        queued = []
        open = false
        player?.cancel(); fader?.cancel(); readbackTask?.cancel()
        asking = false; hovering = false; fadeDue = false
    }
    /// The panel asked to be closed by Isaac (x button): stop the clocks.
    public func closedByUser() { pageGone() }

    private func run(_ js: String) {
        sent.append(js)
        if let e = evaluate { e(js) } else { queued.append(js) }
    }

    /// A slate arrived. Returns the sentences to say, one per block, or nil when it isn't a slate.
    @discardableResult
    public func present(_ j: JSONValue) -> [String]? {
        guard case .array(let blocks)? = j["blocks"], !blocks.isEmpty,
              let data = try? Wire.plainEncoder().encode(j), let text = String(data: data, encoding: .utf8) else { return nil }
        player?.cancel(); fader?.cancel(); readbackTask?.cancel()
        fadeDue = false
        asking = j["ask"]?.stringValue != nil
        title = j["title"]?.stringValue ?? ""
        kind = j["kind"]?.stringValue ?? "quick"
        says = blocks.map { $0["say"]?.stringValue ?? "" }
        lengths = blocks.map { b in max(b["say"]?.stringValue?.count ?? 0, b["tex"]?.stringValue?.count ?? 0) }
        open = true
        run("window.slate.show(\(text))")
        return says
    }

    /// S10: more blocks for the slate on screen. Returns where they start and the sentences to say (nil if nothing is open).
    @discardableResult
    public func append(_ j: JSONValue) -> (Int, [String])? {
        guard open, case .array(let blocks)? = j["blocks"], !blocks.isEmpty,
              let data = try? Wire.plainEncoder().encode(blocks), let text = String(data: data, encoding: .utf8) else { return nil }
        fader?.cancel(); kind = "stay"; fadeDue = false          // a lesson never fades under him
        let from = lengths.count
        let new = blocks.map { $0["say"]?.stringValue ?? "" }
        says += new
        lengths += blocks.map { b in max(b["say"]?.stringValue?.count ?? 0, b["tex"]?.stringValue?.count ?? 0) }
        run("window.slate.append(\(text))")
        return (from, new)
    }

    /// Line `i` starts inking now, over `ms` (the voice path passes the narrator's step; no voice uses `wipeMs`).
    public func reveal(_ i: Int, ms: Int? = nil) {
        guard i < lengths.count else { return }
        let m = ms ?? Self.wipeMs(chars: lengths[i])
        run("window.slate.reveal(\(i), \(m))")
        if i == lengths.count - 1 { scheduleFade(after: Double(m + Self.markMs) / 1000) }
    }

    /// No voice: each line inks in at a steady pace, the next one starting when the last is done.
    public func playSilently(from: Int = 0) {
        player?.cancel()
        let n = lengths.count
        player = Task { [weak self] in
            for i in from..<n {
                guard let self, !Task.isCancelled else { return }
                self.reveal(i)
                let wait = Self.wipeMs(chars: self.lengths[i]) + (i == n - 1 ? 0 : 250)
                try? await Task.sleep(for: .milliseconds(wait))
            }
        }
    }

    /// A quick slate fades `lingerSeconds` after its last word and the window goes. A `stay` slate waits for Isaac.
    /// A protocol (with a read-back) instead asks its question once its last step is written, then waits for his word.
    private func scheduleFade(after secs: Double) {
        fader?.cancel(); readbackTask?.cancel()
        if asking {
            readbackTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(secs))
                guard !Task.isCancelled else { return }
                self?.run("window.slate.readback(1100)")
            }
            return
        }
        guard kind == "quick" else { return }
        fadeDue = true
        guard !hovering else { return }                               // the pointer is on it: wait until it leaves
        fader = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(for: .seconds(secs + self.lingerSeconds))
            guard !Task.isCancelled else { return }
            await self.fadeAway()
        }
    }

    private func fadeAway() async {
        fadeDue = false
        run("window.slate.fade()")
        try? await Task.sleep(for: .seconds(fadeSeconds))
        guard !Task.isCancelled else { return }
        open = false
        onClose?()
    }

    /// The pointer came onto, or left, the page. A quick slate does not fade under it, and fades a linger after it leaves.
    public func hover(_ on: Bool) {
        hovering = on
        if on { fader?.cancel() }
        else if fadeDue && kind == "quick" { scheduleFade(after: 0) }
    }

    /// "wait": a quick slate stays until Isaac closes it.
    public func holdOpen() {
        kind = "stay"; fadeDue = false; fader?.cancel()
    }

    /// "ok": the protocol is done and saved; the page fades and the window goes.
    public func finish() {
        fader?.cancel(); readbackTask?.cancel(); asking = false
        fader = Task { [weak self] in await self?.fadeAway() }
    }

    /// Step `item` of block `block` now says `text`: only that line is re-inked.
    public func update(block: Int, item: Int, text: String) {
        guard let d = try? JSONEncoder().encode(text), let q = String(data: d, encoding: .utf8) else { return }
        run("window.slate.updateItem(\(block), \(item), \(q))")
    }

    /// S9: the history drawer. `history` fetches `slate.list`, `fetchSlate` fetches `slate.get` (both set by the app).
    @ObservationIgnored public var history: (() async -> [JSONValue])?
    @ObservationIgnored public var fetchSlate: ((Int) async -> JSONValue?)?

    public func loadHistory() async {
        let rows = await history?() ?? []
        guard let d = try? Wire.plainEncoder().encode(rows), let t = String(data: d, encoding: .utf8) else { return }
        run("window.slate.setHistory(\(t))")
    }

    /// A past slate, shown finished, or inked again with no voice.
    public func openSaved(_ id: Int, replay: Bool) async {
        guard let j = await fetchSlate?(id), let d = try? Wire.plainEncoder().encode(j), let t = String(data: d, encoding: .utf8) else { return }
        player?.cancel(); fader?.cancel(); readbackTask?.cancel(); starter?.cancel()
        kind = "stay"; asking = false                       // a reopened slate never fades by itself
        run("window.slate.openSaved(\(t), \(replay))")
    }

    /// A pill on the page.
    public func pill(_ a: String) { onPill?(a == "ok" ? "ok" : "I want to change something") }
}
