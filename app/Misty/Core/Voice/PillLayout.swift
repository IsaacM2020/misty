import AppKit
import CoreGraphics
import Foundation

/// V1: the rules for how big the voice pill is and what it shows. Pure numbers and strings, no views, so they are tested
/// without a screen. The pill used to show one line cut off at its head ("...d by 27 is the same number, boss."); now her
/// whole reply is visible while she speaks.
enum PillLayout {
    /// The most lines of her reply the pill shows at once. A longer reply scrolls, following the newest sentence.
    static let replyLines = 6
    /// His words, a status ("Thinking ...") or anything else: two lines, then the end of it, never the head cut off.
    static let otherLines = 2
    /// How long her reply stays up after she stops speaking.
    static let lingerSeconds = 3.0
    /// Line height for the pill's body font (13.5 pt).
    static let lineHeight: CGFloat = 16
    /// Space the dot, the state word and the gaps take before the text column starts.
    static let chrome: CGFloat = 16 + 16 + 9 + 10 + 10 + 62
    static let minWidth: CGFloat = 260
    static let maxWidth: CGFloat = 480
    static let edge: CGFloat = 12

    /// The pill's width: wide enough to read, never wider than the screen it is on (minus a margin on each side).
    static func width(screenWidth: CGFloat) -> CGFloat {
        max(minWidth, min(maxWidth, screenWidth - 2 * (edge + 8)))
    }

    /// The width of the text column inside a pill of that width.
    static func textWidth(screenWidth: CGFloat) -> CGFloat {
        max(120, width(screenWidth: screenWidth) - chrome)
    }

    static func lineCap(reply: Bool) -> Int { reply ? replyLines : otherLines }

    /// The height of the text box: as tall as the text, up to the cap, and never less than one line.
    static func textHeight(content: CGFloat, reply: Bool) -> CGFloat {
        let cap = CGFloat(lineCap(reply: reply)) * lineHeight
        return min(max(content, lineHeight), cap)
    }

    /// True when the text is taller than the box, so the box must follow the newest words.
    static func scrolls(content: CGFloat, reply: Bool) -> Bool {
        content > CGFloat(lineCap(reply: reply)) * lineHeight + 0.5
    }

    /// What the pill says. Her reply wins while she speaks and for a moment after, unless he is talking again (a
    /// transcript line while listening or thinking). Otherwise whatever the line is. The flag says it is her reply.
    static func shown(state: VoiceState, line: String, reply: String, lingering: Bool) -> (text: String, isReply: Bool) {
        if !reply.isEmpty {
            if state == .speaking { return (reply, true) }
            if lingering, line.isEmpty { return (reply, true) }
        }
        return (line, false)
    }

    /// Corner radius: a capsule while it is one line, a soft rounded box when it grows.
    static func corner(height: CGFloat) -> CGFloat { min(height / 2, 20) }

    /// Moves a window frame so it is fully inside the screen's visible area (shrinks it only if it cannot fit).
    static func clamp(_ frame: CGRect, into visible: CGRect) -> CGRect {
        var f = frame
        let area = visible.insetBy(dx: edge, dy: edge)
        f.size.width = min(f.width, area.width)
        f.size.height = min(f.height, area.height)
        f.origin.x = min(max(f.minX, area.minX), area.maxX - f.width)
        f.origin.y = min(max(f.minY, area.minY), area.maxY - f.height)
        return f
    }

    /// Adds a sentence to the reply shown so far.
    static func appended(_ reply: String, _ sentence: String) -> String {
        let s = sentence.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return reply }
        return reply.isEmpty ? s : reply + " " + s
    }

    // MARK: V6, the light glass card

    static let cardCorner: CGFloat = 14
    static let dotSize: CGFloat = 28
    static let oneLine: CGFloat = 30
    static let fontSize: CGFloat = 13
    private static let sidePad: CGFloat = 12
    private static let rightPad: CGFloat = 14
    private static let gap: CGFloat = 8

    /// What the card is showing. One view morphs between these; none of them pops.
    enum Mode: Equatable {
        case dot                    // listening for her name: a small dot, no words
        case hearing(String)        // him talking: waveform and his last few words
        case thinking               // three pulsing dots
        case speaking(String)       // one line: the sentence she is saying now
        case done(String)           // she finished: the last sentence stays a moment
        case note(String)           // a problem or a one-off message ("Mic is off", "Loading her ears")

        var text: String? {
            switch self {
            case .dot, .thinking: return nil
            case .hearing(let t), .speaking(let t), .done(let t), .note(let t): return t
            }
        }
        /// Same key = the same shape of card (only the words change), so the words cross-fade and the size does not spring.
        var key: String {
            switch self {
            case .dot: return "dot"
            case .hearing: return "hearing"
            case .thinking: return "thinking"
            case .speaking: return "speaking"
            case .done: return "done"
            case .note: return "note"
            }
        }
    }

    static func mode(state: VoiceState, line: String, reply: String, lingering: Bool, problem: String?) -> Mode {
        if let p = problem, !p.isEmpty { return .note(p) }
        let l = line.trimmingCharacters(in: .whitespacesAndNewlines)
        switch state {
        case .speaking:
            let one = l.isEmpty ? lastSentence(reply) : lastSentence(l)
            return one.isEmpty ? .dot : .speaking(one)
        case .thinking:
            return .thinking
        case .starting:
            return l.isEmpty ? .dot : .note(l)
        case .listening, .idle:
            if !l.isEmpty, state == .listening { return .hearing(l) }      // V8: the card shows his newest words itself
            if lingering, !reply.isEmpty { return .done(lastSentence(reply)) }
            return .dot
        }
    }

    /// The final sentence of some text ("A. B. C." gives "C."), or the whole of it when it is one sentence.
    static func lastSentence(_ s: String) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let re = try? NSRegularExpression(pattern: #"(?<=[.!?])\s+"#) else { return t }
        let ns = t as NSString
        guard let m = re.matches(in: t, range: NSRange(location: 0, length: ns.length)).last,
              m.range.location + m.range.length < ns.length else { return t }
        return ns.substring(from: m.range.location + m.range.length)
    }

    /// His last few words (the card is one line): "and what about tomorrow in the afternoon" gives the last `count` words.
    static func lastWords(_ s: String, _ count: Int) -> String {
        let w = s.split(whereSeparator: { $0 == " " || $0 == "\n" })
        return w.count <= count ? w.joined(separator: " ") : w.suffix(count).joined(separator: " ")
    }

    /// Hovering a card that has a reply opens it to the whole reply.
    static func expandedText(mode: Mode, reply: String, hovering: Bool) -> String? {
        guard hovering, !reply.isEmpty else { return nil }
        switch mode {
        case .speaking, .done: return reply
        default: return nil
        }
    }

    private static func font() -> NSFont { NSFont.systemFont(ofSize: fontSize) }

    /// The size some text takes in the card's font: wrapped to `width` when given, else on one line.
    static func measure(_ text: String, width: CGFloat? = nil) -> CGSize {
        let a: [NSAttributedString.Key: Any] = [.font: font()]
        if let w = width {
            let r = (text as NSString).boundingRect(with: NSSize(width: w, height: .greatestFiniteMagnitude),
                                                    options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: a)
            return CGSize(width: ceil(r.width), height: ceil(r.height))
        }
        let sz = (text as NSString).size(withAttributes: a)
        return CGSize(width: ceil(sz.width), height: ceil(sz.height))
    }

    /// The width of the icon column for a mode (the dot, the waveform).
    static func iconWidth(_ m: Mode) -> CGFloat {
        switch m {
        case .hearing: return LevelMeter.width
        case .dot, .thinking: return 0
        default: return 9
        }
    }

    /// The text column's width when the card is open on the whole reply.
    static func expandedTextWidth(screenWidth: CGFloat) -> CGFloat {
        width(screenWidth: screenWidth) - sidePad - 9 - gap - rightPad
    }

    /// The text box's height when open: as tall as the reply, up to six lines (the rest scrolls).
    static func expandedTextHeight(_ text: String, screenWidth: CGFloat) -> CGFloat {
        min(measure(text, width: expandedTextWidth(screenWidth: screenWidth)).height, CGFloat(replyLines) * lineHeight)
    }

    /// The card itself. Small for a dot, a pill of one line for words, tall only when hovered on a reply.
    /// V8: while he talks the card is one steady width (words rise in inside it, it never jitters per word).
    static let hearingWidth: CGFloat = 300

    /// Her sentences, split where one ends.
    static func sentences(_ s: String) -> [String] {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, let re = try? NSRegularExpression(pattern: #"(?<=[.!?])\s+"#) else { return t.isEmpty ? [] : [t] }
        let ns = t as NSString
        var out: [String] = [], from = 0
        for m in re.matches(in: t, range: NSRange(location: 0, length: ns.length)) {
            out.append(ns.substring(with: NSRange(location: from, length: m.range.location - from)))
            from = m.range.location + m.range.length
        }
        if from < ns.length { out.append(ns.substring(from: from)) }
        return out
    }

    static func cardSize(mode: Mode, expanded: String?, screenWidth: CGFloat, reply: String = "") -> CGSize {
        if let e = expanded {
            return CGSize(width: width(screenWidth: screenWidth), height: expandedTextHeight(e, screenWidth: screenWidth) + 18)
        }
        switch mode {
        case .dot: return CGSize(width: dotSize, height: dotSize)
        case .thinking: return CGSize(width: 54, height: oneLine)
        case .hearing: return CGSize(width: min(hearingWidth, width(screenWidth: screenWidth)), height: oneLine)
        default:
            let t = mode.text ?? ""
            var widest = measure(t).width
            if case .speaking = mode { widest = max(widest, sentences(reply).map { measure($0).width }.max() ?? 0) }  // V8: grows, never jumps back
            if case .done = mode { widest = max(widest, sentences(reply).map { measure($0).width }.max() ?? 0) }
            let natural = sidePad + iconWidth(mode) + gap + widest + rightPad
            return CGSize(width: max(dotSize, min(natural, width(screenWidth: screenWidth))), height: oneLine)
        }
    }

    /// The window is the card plus a hair of room. It grows at once and shrinks after the card has finished morphing.
    static func canvas(_ card: CGSize) -> CGSize { CGSize(width: card.width + 2, height: card.height + 2) }

    /// How far the whole-reply box is scrolled: 0 follows the newest words (the bottom), larger moves up, never past the top.
    static func scrollOffset(requested: CGFloat, contentHeight: CGFloat, boxHeight: CGFloat) -> CGFloat {
        max(0, min(requested, max(0, contentHeight - boxHeight)))
    }
}
