import Foundation

/// The last stop before her voice: turns the few things the voice model reads badly into words a person would say.
/// Each rule is here because a round trip (speak it, then transcribe it with her ears, 2026-11-12) showed the model
/// getting it wrong: "22:15" came out "twenty two fifteen", "2026-11-18" as loose digits, "_" as "underscore",
/// "4-1" as "forty one", "e.g." as "E G", and a link was spelled out letter by letter.
/// The prompt already asks for spoken style; this catches what slips through. Plain words pass untouched.
public enum SpokenText {
    private static let months = ["January", "February", "March", "April", "May", "June", "July", "August",
                                 "September", "October", "November", "December"]

    public static func clean(_ s: String) -> String {
        var r = s
        r = replace(r, #"https?://(?:www\.)?([A-Za-z0-9.-]+)[^\s)\]]*"#) { m in "a link on " + site(m[1]) }
        r = lines(r)
        r = maths(r)
        r = replace(r, #"\b(\d{4})-(\d{2})-(\d{2})\b"#) { m in                                   // 2026-11-18 -> 18 November
            guard let mo = Int(m[2]), (1...12).contains(mo), let d = Int(m[3]), (1...31).contains(d) else { return m[0] }
            return "\(d) \(months[mo - 1])"
        }
        // Only clock times that can't be anything else (13:00 to 23:59, 00:xx): "9:05" already reads fine and "1:30" may be a length.
        r = replace(r, #"\b(1[3-9]|2[0-3]|00):([0-5]\d)\b"#) { m in clock(Int(m[1])!, Int(m[2])!) }
        r = replace(r, #"\b(\d{4})-(\d{2})\b"#) { m in "\(m[1]) to \(m[2])" }                       // a school year: 2026-27
        r = replace(r, #"(?<![\w-])(\d{1,2})-(\d{1,2})(?![\w-])"#) { m in "\(m[1]) to \(m[2])" }    // a score or a range: 4-1, 5-10
        r = replace(r, #"(?<=\d)(?=[A-Z]{2})"#) { _ in " " }                                       // 11IB -> 11 IB
        let words: [(String, String)] = [
            (#"\be\.g\.,?"#, "for example"), (#"\bi\.e\.,?"#, "that is"),
            (#"\bvs\.?(?=\s)"#, "versus"), (#"\bw/(?=\s)"#, "with"),
        ]
        r = replace(r, #"\b(?i:etc)\.(?=\s*$|\s+[A-Z])"#) { _ in "and so on." }                   // "etc." that also ends the sentence
        r = replace(r, #"\b(?i:etc)\b\.?"#) { _ in "and so on" }
        for (pattern, word) in words { r = replace(r, pattern, options: [.caseInsensitive]) { _ in word } }
        r = r.replacingOccurrences(of: "_", with: " ")
        r = replace(r, #"[*`#]+"#) { _ in "" }                                                     // markdown that slipped in
        r = String(String.UnicodeScalarView(r.unicodeScalars.filter { !isEmoji($0) }))
        r = r.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .replacingOccurrences(of: #"\s+([,.!?])"#, with: "$1", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        return splitLong(r)
    }

    // MARK: V2, written for the ear

    /// A list or a few lines become one run of sentences: markers and heading hashes go, each line ends like a sentence.
    static func lines(_ s: String) -> String {
        guard s.contains("\n") else { return s }
        var r = replace(s, #"(?m)^[ \t]*#{1,6}[ \t]+"#) { _ in "" }
        r = replace(r, #"(?m)^[ \t]*(?:[-\u2022*]|\d{1,2}[.)])[ \t]+"#) { _ in "" }
        let parts = r.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        var out = ""
        for (i, line) in parts.enumerated() {
            out += line
            if i < parts.count - 1 { out += (line.last.map { ".!?:,".contains($0) } ?? false) ? " " : ". " }
        }
        return out
    }

    /// Symbols and maths as a person reads them aloud: "27/48" is "27 over 48", "x^2" is "x squared", "=" is "equals".
    static func maths(_ s: String) -> String {
        var r = s
        r = replace(r, #"(?<=\d)(?:\s*[*\u00D7]\s*)(?=\d)"#) { _ in " times " }
        r = replace(r, #"\s*[\u00D7]\s*"#) { _ in " times " }
        r = replace(r, #"\s*\u00F7\s*"#) { _ in " divided by " }
        r = replace(r, #"\s*\u2248\s*"#) { _ in " about " }
        r = replace(r, #"\s*\u2260\s*"#) { _ in " is not equal to " }
        r = replace(r, #"\s*\u2265\s*"#) { _ in " greater than or equal to " }
        r = replace(r, #"\s*\u2264\s*"#) { _ in " less than or equal to " }
        r = replace(r, #"(?<=[\w)])\s*<\s*(?=[\w(])"#) { _ in " less than " }
        r = replace(r, #"(?<=[\w)])\s*>\s*(?=[\w(])"#) { _ in " greater than " }
        r = replace(r, #"\^2(?!\d)|\u00B2"#) { _ in " squared" }
        r = replace(r, #"\^3(?!\d)|\u00B3"#) { _ in " cubed" }
        r = replace(r, #"\^(\{?)(\w+)\}?"#) { m in " to the power " + m[2] }
        r = replace(r, #"\u221A\s*"#) { _ in "square root of " }
        r = replace(r, #"\u03C0"#) { _ in "pi" }
        r = replace(r, #"\s*\u00B0\s*C\b"#) { _ in " degrees Celsius" }
        r = replace(r, #"\s*\u00B0\s*F\b"#) { _ in " degrees Fahrenheit" }
        r = replace(r, #"\s*\u00B0"#) { _ in " degrees" }
        r = replace(r, #"(?<![\w/.])(\d+[a-z]?)/(\d+|\()(?![\d/])"#) { m in m[1] + " over " + m[2] }
        r = replace(r, #"(?<!\w)([a-z])-(?=\d)"#) { m in m[1] + " minus " }                      // x-2
        r = replace(r, #"(?<=[\d)]|squared|cubed)\s+-\s+(?=[\d(])"#) { _ in " minus " }        // 5 - 3, x squared - 4
        r = replace(r, #"(?<=[\w)])\s*\+\s*(?=[\w(])"#) { _ in " plus " }
        r = replace(r, #"\s*=(?![>=])\s*"#) { _ in " equals " }
        r = replace(r, #"(?<=\d)\u2013(?=\d)"#) { _ in " to " }
        r = replace(r, #"\s*[\u2014\u2013]\s*"#) { _ in ", " }
        r = replace(r, #";\s*"#) { _ in ". " }
        return r
    }

    /// A sentence of more than 28 words is cut at its ", and" / ", but" / ", so" nearest the middle (both halves at least
    /// 8 words), so the voice breathes. Short sentences pass untouched.
    static func splitLong(_ s: String) -> String {
        sentenceList(s).map(cut).joined(separator: " ")
    }

    private static func sentenceList(_ s: String) -> [String] {
        guard let re = try? NSRegularExpression(pattern: #"(?<=[.!?])\s+"#) else { return [s] }
        let ns = s as NSString
        var out: [String] = [], last = 0
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            out.append(ns.substring(with: NSRange(location: last, length: m.range.location - last)))
            last = m.range.location + m.range.length
        }
        out.append(ns.substring(from: last))
        return out
    }

    private static func cut(_ sentence: String) -> String {
        let words = sentence.split(separator: " ")
        guard words.count > 28, let re = try? NSRegularExpression(pattern: #",\s+(and|but|so)\s"#) else { return sentence }
        let ns = sentence as NSString
        var best: (NSRange, Int)?
        for m in re.matches(in: sentence, range: NSRange(location: 0, length: ns.length)) {
            let before = ns.substring(to: m.range.location).split(separator: " ").count
            let after = words.count - before
            guard before >= 8, after >= 8 else { continue }
            if best == nil || abs(before - words.count / 2) < abs(best!.1 - words.count / 2) { best = (m.range, before) }
        }
        guard let (range, _) = best else { return sentence }
        let word = ns.substring(with: NSRange(location: range.location + 1, length: range.length - 1)).trimmingCharacters(in: .whitespaces)
        let head = ns.substring(to: range.location) + "."
        let tail = word.prefix(1).uppercased() + word.dropFirst() + " " + ns.substring(from: range.location + range.length)
        return cut(head) + " " + cut(tail)
    }

    /// "22:15" the way it is said: "10:15 pm", "7 pm", "12:05 am", "midnight".
    static func clock(_ h: Int, _ m: Int) -> String {
        if m == 0, h == 12 { return "noon" }
        if m == 0, h == 0 { return "midnight" }
        let half = h < 12 ? "am" : "pm"
        let h12 = h % 12 == 0 ? 12 : h % 12
        return m == 0 ? "\(h12) \(half)" : "\(h12):\(m < 10 ? "0\(m)" : "\(m)") \(half)"
    }

    /// "bbc.com" from "www.bbc.com" or "news.bbc.co.uk": the last name before the ending, said as "bbc dot com".
    private static func site(_ host: String) -> String {
        host.replacingOccurrences(of: ".", with: " dot ")
    }

    private static func isEmoji(_ u: Unicode.Scalar) -> Bool {
        u.value == 0xFE0F || u.value == 0x200D || (u.properties.isEmojiPresentation && u.value > 0xFF)
            || (0x1F000...0x1FAFF).contains(u.value)
    }

    private static func replace(_ s: String, _ pattern: String, options: NSRegularExpression.Options = [],
                                _ with: ([String]) -> String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern, options: options) else { return s }
        let ns = s as NSString
        var out = "", last = 0
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            out += with((0..<m.numberOfRanges).map { m.range(at: $0).location == NSNotFound ? "" : ns.substring(with: m.range(at: $0)) })
            last = m.range.location + m.range.length
        }
        return out + ns.substring(from: last)
    }
}
