import Foundation

/// Cuts a streamed chat reply into sentences the Speaker can say while the rest is still arriving.
/// Feed it each `chat.delta` piece; it hands back whole sentences, ending at . ! ? or a newline,
/// or at a comma once a sentence runs past `commaAfter` characters. `flush()` at the end of the turn
/// returns whatever is left. A terminator at the very end of the buffer waits for the next piece,
/// so "3." + "5" stays "3.5"; "e.g." and decimals never cut.
/// A chunk of 3 words or fewer is never handed out alone ("boss." after a long clause): a comma only cuts when 4+ words
/// follow it, a short chunk merges into its neighbour, and after the first chunk one chunk is held back so a short tail
/// can still join it. The first chunk goes out at once unless it is short and not a complete sentence ("One sec." before a tool call must not wait).
public struct SentenceSplitter: Sendable {
    public static let minWords = 4
    public var commaAfter: Int
    private var buf: [Character] = []
    private var held: String?
    private var emittedAny = false

    private static let terminators: Set<Character> = [".", "!", "?"]
    private static let closers: Set<Character> = ["\"", "'", ")", "]", "\u{201D}", "\u{2019}"]
    private static let abbreviations: Set<String> = ["e.g", "i.e", "mr", "mrs", "ms", "dr", "vs", "st", "approx", "no"]

    public init(commaAfter: Int = 120) { self.commaAfter = commaAfter }

    public mutating func feed(_ piece: String) -> [String] {
        buf.append(contentsOf: piece)
        var out: [String] = []
        while let end = cutIndex() {
            let s = String(buf[..<end]).trimmingCharacters(in: .whitespacesAndNewlines)
            buf.removeFirst(end)
            if !s.isEmpty { out.append(contentsOf: accept(s)) }
        }
        return out
    }

    static func wordCount(_ s: String) -> Int { s.split(whereSeparator: \.isWhitespace).count }
    private static func isShort(_ s: String) -> Bool { wordCount(s) < minWords }

    private static func endsSentence(_ s: String) -> Bool {
        guard let c = s.last(where: { !closers.contains($0) }) else { return false }
        return terminators.contains(c)
    }

    /// Applies the no-tiny-chunk rule to one raw chunk; returns what may be spoken now.
    private mutating func accept(_ s: String) -> [String] {
        var out: [String] = []
        var cur = s
        if let h = held {
            held = nil
            if Self.isShort(h) || Self.isShort(s) { cur = h + " " + s } else { out.append(h) }
        }
        if !emittedAny, !Self.isShort(cur) || Self.endsSentence(cur) { out.append(cur); emittedAny = true } else { held = cur }
        return out
    }

    /// End of the reply: whatever is buffered and held, merged (nothing if blank). Safe to call again: the second call is empty.
    public mutating func flush() -> [String] {
        let s = String(buf).trimmingCharacters(in: .whitespacesAndNewlines)
        buf.removeAll()
        var out = s.isEmpty ? [] : accept(s)
        if let h = held { out.append(h) }
        held = nil
        emittedAny = false
        return out
    }

    /// Where the first complete sentence in `buf` ends (exclusive), or nil if none is complete yet.
    private func cutIndex() -> Int? {
        let c = buf
        var i = 0
        while i < c.count {
            let ch = c[i]
            if ch == "\n" { return i + 1 }
            if Self.terminators.contains(ch) {
                var j = i + 1
                while j < c.count, Self.terminators.contains(c[j]) { j += 1 }   // "?!" and "..."
                while j < c.count, Self.closers.contains(c[j]) { j += 1 }       // the quote or bracket it closes
                if j == c.count { return nil }                                  // the next piece decides
                if c[j].isWhitespace, !(ch == "." && j == i + 1 && noCut(before: i)) { return j }
                i = j
                continue
            }
            if ch == ",", i >= commaAfter, i + 1 < c.count, c[i + 1].isWhitespace, wordsAfter(i) >= Self.minWords { return i + 1 }
            i += 1
        }
        return nil
    }

    /// Whole words after position `i` before the next . ! ? or newline (a word counts once a space follows it).
    private func wordsAfter(_ i: Int) -> Int {
        var n = 0, inWord = false
        for ch in buf[(i + 1)...] {
            if ch == "\n" || Self.terminators.contains(ch) { return n }
            if ch.isWhitespace { if inWord { n += 1; inWord = false } } else { inWord = true }
        }
        return n
    }

    /// A single "." that ends an abbreviation ("e.g.", "Dr.") or a list number at the start ("1."), not a sentence.
    private func noCut(before dot: Int) -> Bool {
        var k = dot
        while k > 0, !buf[k - 1].isWhitespace { k -= 1 }
        let word = String(buf[k..<dot]).lowercased()
        if Self.abbreviations.contains(word) { return true }
        let atStart = buf[..<k].allSatisfy(\.isWhitespace)
        return atStart && !word.isEmpty && word.allSatisfy(\.isNumber)
    }
}
