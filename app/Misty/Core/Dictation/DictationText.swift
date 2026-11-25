import AppKit
import Carbon.HIToolbox      // IsSecureEventInputEnabled

/// What dictation does to the words before they land. Light and local: the speaker's words survive exactly.
/// (Ripple 2 sent the text to a Groq model for this. Misty keeps what she hears on this Mac, so it is done here.)
enum DictationText {
    private static let fillers = try! NSRegularExpression(pattern: #"(?<![\p{L}'])(?:u+h+m*|u+m+|e+r+m+|h+m+)(?![\p{L}'])[,.]?\s*"#, options: .caseInsensitive)
    private static let stutter = try! NSRegularExpression(
        pattern: #"(?<![\p{L}'])(the|a|an|i|to|and|of|in|is|it|we|you|my|so|but|or|for|on|at)(?:\s+\1)+(?![\p{L}'])"#, options: .caseInsensitive)

    /// Words the ears get wrong on every pass, because they know nothing about Isaac's world: a name they've never
    /// seen comes out as the nearest real word. Fixed after the read, on this Mac. "of socks" is only "of sorts" when
    /// no pair, bag or drawer comes before it.
    private static let alwaysWrong: [(heard: String, wanted: String)] = [
        (#"(?<!\b(?:pair|pairs|couple|lot|lots|bunch|bag|bags|box|boxes|drawer|pile|stack|many|few|full|dozen|loads|hundreds|thousands)\s)of socks"#, "of sorts"),
        (#"\b(?:groq|grok|grock|groc|drock|grook)\b"#, "Groq"),
        (#"\bmisty\b"#, "Misty"),
    ]

    /// His own list: one `heard => wanted` per line in ~/Misty/dictionary.txt, whole words, any capitalisation.
    /// Missing file means no extras. Read on every dictation, so an edit takes effect on the next one.
    static func userWords(path: String = NSHomeDirectory() + "/Misty/dictionary.txt") -> [(heard: String, wanted: String)] {
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return [] }
        return text.split(whereSeparator: \.isNewline).compactMap { line in
            let parts = line.components(separatedBy: "=>").map { $0.trimmingCharacters(in: .whitespaces) }
            guard parts.count == 2, !parts[0].isEmpty, !parts[0].hasPrefix("#") else { return nil }
            return (#"\b"# + NSRegularExpression.escapedPattern(for: parts[0]) + #"\b"#, parts[1])
        }
    }

    /// Removes um/uh and doubled little words ("the the"), fixes the words on the lists above, tidies spaces,
    /// capitalises the first letter. Otherwise never rewords.
    static func clean(_ raw: String, extra: [(heard: String, wanted: String)]? = nil) -> String {
        var t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        t = replace(fillers, in: t, with: "")
        t = replace(stutter, in: t, with: "$1")
        for (heard, wanted) in (extra ?? userWords()) + alwaysWrong { t = fix(heard, wanted, in: t) }
        t = t.replacingOccurrences(of: #"\s{2,}"#, with: " ", options: .regularExpression)
        t = t.replacingOccurrences(of: #"\s+([,.!?;:])"#, with: "$1", options: .regularExpression)
        t = t.trimmingCharacters(in: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",")))
        if let f = t.first, f.isLowercase { t = f.uppercased() + t.dropFirst() }
        return t
    }

    /// One heard -> wanted swap. A capital at the start of what was heard carries over to the replacement ("Of socks" -> "Of sorts").
    private static func fix(_ pattern: String, _ wanted: String, in s: String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive) else { return s }
        var out = s
        for m in re.matches(in: s, range: NSRange(s.startIndex..., in: s)).reversed() {
            guard let r = Range(m.range, in: out) else { continue }
            var w = wanted
            if out[r].first?.isUppercase == true, let f = w.first, f.isLowercase { w = f.uppercased() + w.dropFirst() }
            out.replaceSubrange(r, with: w)
        }
        return out
    }

    private static func replace(_ re: NSRegularExpression, in s: String, with tpl: String) -> String {
        re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: tpl)
    }
}

/// Opt-in, for tuning the ears on his real voice. While the file `~/Misty/dictation-keep` exists, each dictation is
/// saved in `~/Misty/dictation-samples/` as a wav (exactly what the model heard) next to a txt with what it said.
/// Never leaves this Mac, never sent anywhere. Delete the file to stop; the newest 100 are kept.
enum DictationSamples {
    static let flag = NSHomeDirectory() + "/Misty/dictation-keep"
    static let folder = NSHomeDirectory() + "/Misty/dictation-samples"
    static let keep = 100

    static func keepIfOn(_ wave: [Float], raw: String, cleaned: String) {
        let fm = FileManager.default
        guard fm.fileExists(atPath: flag) else { return }
        try? fm.createDirectory(atPath: folder, withIntermediateDirectories: true)
        let stamp = String(Int(Date().timeIntervalSince1970 * 1000))
        let base = folder + "/dictation-" + stamp
        var d = Data()
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        let n = UInt32(wave.count * 4)                       // 32-bit float, so a hot mic is kept as it was
        d.append(contentsOf: Array("RIFF".utf8)); u32(36 + n); d.append(contentsOf: Array("WAVEfmt ".utf8))
        u32(16); u16(3); u16(1); u32(16000); u32(64000); u16(4); u16(32)
        d.append(contentsOf: Array("data".utf8)); u32(n)
        wave.withUnsafeBufferPointer { p in d.append(UnsafeBufferPointer(start: UnsafeRawPointer(p.baseAddress!).assumingMemoryBound(to: UInt8.self), count: wave.count * 4)) }
        try? d.write(to: URL(fileURLWithPath: base + ".wav"))
        try? "heard: \(raw)\npasted: \(cleaned)\n".write(toFile: base + ".txt", atomically: true, encoding: .utf8)
        // newest 100 only
        let files = ((try? fm.contentsOfDirectory(atPath: folder)) ?? []).filter { $0.hasSuffix(".wav") }.sorted()
        for old in files.dropLast(keep) {
            try? fm.removeItem(atPath: folder + "/" + old)
            try? fm.removeItem(atPath: folder + "/" + old.replacingOccurrences(of: ".wav", with: ".txt"))
        }
    }
}

/// Puts the text where the cursor is: paste, then give the clipboard back. Copy only when there is no text field
/// (or it is a password field) so a sentence is never typed into the wrong place.
@MainActor
enum DictationDelivery {
    enum Result { case pasted, copied(reason: String) }

    static func deliver(_ text: String) -> Result {
        if IsSecureEventInputEnabled() { copy(text); return .copied(reason: "password field") }
        guard AXIsProcessTrusted() else { copy(text); return .copied(reason: "needs Accessibility") }
        guard hasTextFocus() else { copy(text); return .copied(reason: "no text field") }
        paste(text)
        return .pasted
    }

    private static func hasTextFocus() -> Bool {
        var focused: AnyObject?
        guard AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), kAXFocusedUIElementAttribute as CFString, &focused) == .success,
              let element = focused else { return false }
        let ax = element as! AXUIElement            // a CFTypeRef that AX says is an element
        var range: AnyObject?
        if AXUIElementCopyAttributeValue(ax, kAXSelectedTextRangeAttribute as CFString, &range) == .success { return true }
        var role: AnyObject?
        guard AXUIElementCopyAttributeValue(ax, kAXRoleAttribute as CFString, &role) == .success, let r = role as? String else { return false }
        return r == kAXTextFieldRole || r == kAXTextAreaRole || r == kAXComboBoxRole
    }

    private static func copy(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }

    private static func paste(_ text: String) {
        let pb = NSPasteboard.general
        let saved: [[NSPasteboard.PasteboardType: Data]] = (pb.pasteboardItems ?? []).map { item in
            var d: [NSPasteboard.PasteboardType: Data] = [:]
            for t in item.types { if let data = item.data(forType: t) { d[t] = data } }
            return d
        }
        pb.clearContents()
        pb.setString(text, forType: .string)
        let src = CGEventSource(stateID: .combinedSessionState)
        guard let down = CGEvent(keyboardEventSource: src, virtualKey: 9, keyDown: true),       // 9 = "v"
              let up = CGEvent(keyboardEventSource: src, virtualKey: 9, keyDown: false) else { return }
        for e in [down, up] {
            e.flags = .maskCommand
            e.setIntegerValueField(.eventSourceUserData, value: Input.tag)    // so Misty's own key watchers ignore it
            e.post(tap: .cgAnnotatedSessionEventTap)
        }
        guard !saved.isEmpty else { return }
        Task { @MainActor in                                     // give the clipboard back once the app has taken the paste
            try? await Task.sleep(for: .milliseconds(300))
            pb.clearContents()
            for contents in saved {
                let item = NSPasteboardItem()
                for (t, data) in contents { item.setData(data, forType: t) }
                pb.writeObjects([item])
            }
        }
    }
}
