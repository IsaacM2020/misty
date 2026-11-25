import Foundation

/// Reads a drawing's captions aloud one step at a time: show step 1, say its caption, wait until she has finished
/// saying it, show step 2, and so on, so the picture builds as she talks. Whatever the chat reply says while this
/// runs is held and said after the last caption (the speaker is one queue, so the two would otherwise interleave).
/// A tap (cancel) ends it at once.
@MainActor
final class Narrator {
    private(set) var active = false
    /// The caption being shown or said now (set before `show`), so audio events can be matched to a step.
    private(set) var step = 0
    /// Called once when a tap, barge-in or end cuts the narration short (the Slate finishes its current line).
    var onCancel: (() -> Void)?
    private var task: Task<Void, Never>?
    private var gate: CheckedContinuation<Void, Never>?
    private var held: [String] = []
    var breath: Double = 0.25            // a short rest between steps
    var silentStep: Double = 1.4         // how long a step with no caption (or no speech) stays up

    /// While narrating, a reply sentence waits here. Returns true when it was taken.
    func hold(_ sentence: String) -> Bool {
        guard active else { return false }
        held.append(sentence)
        return true
    }

    /// The speaker finished what it was saying: the current step is done.
    func spoke() {
        let g = gate
        gate = nil
        g?.resume()
    }

    func cancel() {
        let was = active
        active = false
        task?.cancel()
        task = nil
        held = []
        spoke()
        let cb = onCancel
        onCancel = nil
        if was { cb?() }
    }

    /// `speak` starts saying a caption and returns whether speech really began (false: no voice, so just wait a beat).
    func run(_ captions: [String], show: @escaping @MainActor (Int) -> Void, speak: @escaping @MainActor (String) -> Bool,
             silent: (@MainActor (Int) -> Void)? = nil, stopped: (@MainActor () -> Void)? = nil,
             done: @escaping @MainActor ([String]) -> Void) {
        cancel()
        onCancel = stopped
        active = true
        task = Task { [weak self] in
            for (i, caption) in captions.enumerated() {
                guard let self, self.active, !Task.isCancelled else { return }
                self.step = i
                show(i)
                let text = Speakable.text(caption)
                if !text.isEmpty, speak(text) {
                    await withCheckedContinuation { self.gate = $0 }
                } else {
                    silent?(i)
                    try? await Task.sleep(for: .seconds(self.silentStep))
                }
                guard self.active, !Task.isCancelled else { return }
                try? await Task.sleep(for: .seconds(self.breath))
            }
            guard let self, self.active else { return }
            let rest = self.held
            self.held = []
            self.active = false
            self.onCancel = nil
            done(rest)
        }
    }
}

/// A caption written for the eye ("3x^2 + 2 = 14") turned into words for the ear.
enum Speakable {
    static func text(_ s: String) -> String {
        var r = s.trimmingCharacters(in: .whitespacesAndNewlines)
        let regex: [(String, String)] = [
            (#"\b([fgh])\("#, "$1 of ("),                       // f(x) -> f of x
            (#"(?<![\w)])-(?=[\w(])"#, "negative "),            // -2 -> negative 2
            (#"(?<=[\w)])\s*-\s*(?=[\w(])"#, " minus "),        // x-2 -> x minus 2
            (#"(?<=[\w)])\s*/\s*(?=[\w(])"#, " over "),         // 3x/(x-2) -> 3x over x minus 2
        ]
        for (pattern, template) in regex {
            r = r.replacingOccurrences(of: pattern, with: template, options: .regularExpression)
        }
        let plain: [(String, String)] = [
            ("^2", " squared"), ("^3", " cubed"), ("²", " squared"), ("³", " cubed"), ("<=", " is at most "),
            (">=", " is at least "), ("≤", " is at most "), ("≥", " is at least "), ("=", " equals "), ("*", " times "),
            ("·", " times "), ("+", " plus "), ("sqrt", " square root of "), ("√", " square root of "), ("π", " pi "),
            ("(", " "), (")", " "),
        ]
        for (a, b) in plain { r = r.replacingOccurrences(of: a, with: b) }
        return r.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
    }
}
