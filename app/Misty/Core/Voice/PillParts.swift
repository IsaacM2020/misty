import AppKit
import SwiftUI

/// V8: one motion language for all of EDITH. Every spring is critically damped (bounce 0): things glide to where they
/// are going and stop, they never wobble past it. Nothing loops forever unless it is showing something real.
enum Motion {
    static let morph = Animation.spring(duration: 0.38, bounce: 0)      // a shape changing size (the pills, cards)
    static let quick = Animation.spring(duration: 0.22, bounce: 0)      // presses and hovers
    static let settle = Animation.spring(duration: 0.5, bounce: 0)      // lists, pages, steps
    static let calm = Animation.easeInOut(duration: 0.2)                // what Reduce Motion gets instead
    static var reduce: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }
    static func or(_ a: Animation) -> Animation { reduce ? calm : a }
}

/// A thing arriving: from a touch of blur, a hair small and a few points low, into place. Leaving is the reverse.
struct ArriveModifier: ViewModifier {
    let on: Bool
    var dy: CGFloat = 3
    func body(content: Content) -> some View {
        content.opacity(on ? 1 : 0).blur(radius: on ? 0 : 3).scaleEffect(on ? 1 : 0.96).offset(y: on ? 0 : dy)
    }
}

extension AnyTransition {
    static var arrive: AnyTransition { .modifier(active: ArriveModifier(on: false), identity: ArriveModifier(on: true)) }
    /// A word leaving drifts up a little instead of down.
    static var word: AnyTransition {
        .asymmetric(insertion: .modifier(active: ArriveModifier(on: false, dy: 4), identity: ArriveModifier(on: true)),
                    removal: .modifier(active: ArriveModifier(on: false, dy: -2), identity: ArriveModifier(on: true)))
    }
}

/// Mic loudness to a calm 0...1 meter: a decibel scale (how loudness is heard), quick to rise with the voice, slower to fall.
struct LevelSmoother: Equatable {
    static let bars = 5
    private(set) var value: Float = 0
    /// Newest first. The meter reads outward from its middle, so a syllable ripples out.
    private(set) var history: [Float] = Array(repeating: 0, count: 3)

    init() {}
    /// Tests and screenshots: a meter frozen mid-word.
    init(test history: [Float]) { self.history = history; value = history.first ?? 0 }

    mutating func push(_ rms: Float) {
        let db = 20 * log10(max(rms, 1e-7))
        let target = max(0, min(1, (db + 55) / 45))              // -55 dBFS ... -10 dBFS -> 0 ... 1
        value += (target - value) * (target > value ? 0.5 : 0.12)
        history.removeLast()
        history.insert(value, at: 0)
    }

    mutating func reset() { value = 0; history = Array(repeating: 0, count: history.count) }

    static func rms(_ s: [Float]) -> Float {
        guard !s.isEmpty else { return 0 }
        return (s.reduce(Float(0)) { $0 + $1 * $1 } / Float(s.count)).squareRoot()
    }
}

/// Five bars driven by the real mic: still when he is quiet, rippling out from the middle when he talks.
struct LevelMeter: View {
    let history: [Float]
    nonisolated static let width: CGFloat = 5 * 3 + 4 * 2.5
    private let maxHeight: CGFloat = 14

    var body: some View {
        HStack(spacing: 2.5) {
            ForEach(0..<LevelSmoother.bars, id: \.self) { i in
                let d = abs(i - LevelSmoother.bars / 2)
                let level = CGFloat(history.isEmpty ? 0 : history[min(d, history.count - 1)])
                let h = max(3, level * maxHeight * (1 - CGFloat(d) * 0.18))
                Capsule().fill(Theme.lime).frame(width: 3, height: h)
                    .animation(Motion.reduce ? nil : .spring(duration: 0.14, bounce: 0), value: h)
            }
        }
        .frame(width: Self.width, height: maxHeight)
    }
}

/// His words as he says them, on one line: each new word rises into place, and older ones slide out at the left edge
/// (soft-faded, never cut hard). A word the model corrects re-arrives; the ones that stayed put don't move.
struct LiveWords: View {
    let text: String
    var keep = 9

    struct Word: Identifiable, Equatable { let id: String; let text: String }

    static func words(_ text: String, keep: Int) -> [Word] {
        let all = text.split(whereSeparator: \.isWhitespace).map(String.init)
        let start = max(0, all.count - keep)
        return all.enumerated().dropFirst(start).map { Word(id: "\($0.offset)-\($0.element)", text: $0.element) }
    }

    var body: some View {
        let ws = Self.words(text, keep: keep)
        let natural = PillLayout.measure(ws.map(\.text).joined(separator: " ")).width
        // The words sit in an overlay, so however many there are they never push the meter or the card around. While they
        // fit they start at the left; once they don't, the newest stay pinned right and the oldest slide out softly.
        Color.clear
            .frame(maxWidth: .infinity, minHeight: 16, maxHeight: 16)
            .overlay {
                GeometryReader { g in
                    let overflow = natural > g.size.width
                    HStack(spacing: 3.5) {
                        ForEach(ws) { w in
                            Text(w.text).font(.system(size: PillLayout.fontSize)).foregroundStyle(.primary)
                                .fixedSize().transition(.word)
                        }
                    }
                    .fixedSize()
                    .frame(width: g.size.width, height: g.size.height, alignment: overflow ? .trailing : .leading)
                    .mask(LinearGradient(stops: [.init(color: overflow ? .clear : .black, location: 0), .init(color: .black, location: 0.14),
                                                 .init(color: .black, location: 1)], startPoint: .leading, endPoint: .trailing))
                }
            }
            .clipped()
            .animation(Motion.or(Motion.morph), value: ws)
    }
}

/// Three dots that softly take turns, opacity only (no bouncing), while something is being worked out.
struct ThreeDots: View {
    let reduceMotion: Bool
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { tl in
            let t = tl.date.timeIntervalSinceReferenceDate
            HStack(spacing: 5) {
                ForEach(0..<3, id: \.self) { i in
                    let p = reduceMotion ? 0.6 : 0.5 + 0.5 * sin(t * 3.4 - Double(i) * 0.8)
                    Circle().fill(Theme.lime).frame(width: 6, height: 6).opacity(0.3 + 0.7 * p)
                }
            }
        }
    }
}

/// Reads the recording so far about twice a second while he talks, so his words show up live. One read at a time, only
/// the last 12 s (the pill shows his newest words), always on Phonon-2. The final read after he lets go is separate.
@MainActor
final class LiveReader {
    private var task: Task<Void, Never>?
    static let every: Duration = .milliseconds(420)

    func start(ears: Ears, snapshot: @escaping @Sendable () -> [Float], onText: @escaping @MainActor (String) -> Void) {
        stop()
        task = Task { [weak ears] in
            var lastCount = 0
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.every)
                guard !Task.isCancelled, let ears, ears.ready else { continue }
                let wave = snapshot()
                guard wave.count >= 8000, wave.count != lastCount else { continue }        // half a second of speech first
                lastCount = wave.count
                guard wave.reduce(Float(0), { max($0, abs($1)) }) >= 0.01 else { continue }
                let text = DictationText.clean(await ears.readPartial(wave))
                guard !Task.isCancelled else { return }
                if text.contains(where: \.isLetter) { onText(text) }
            }
        }
    }

    func stop() { task?.cancel(); task = nil }
}
