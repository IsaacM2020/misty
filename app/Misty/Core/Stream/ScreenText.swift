import Foundation
import ApplicationServices
import CoreGraphics
import ImageIO

/// Level 2 of seeing (step C): the text of his window, free. Accessibility first; the engine falls back to OCR when this
/// comes back thin. Nothing here is stored or logged.
public enum ScreenText {
    public static let cap = 8000
    public static let mark = ("⟦", "⟧")

    /// Strings in reading order, consecutive duplicates dropped, blank ones skipped.
    public static func join(_ pieces: [String]) -> String {
        var out: [String] = []
        for p in pieces {
            let t = p.trimmingCharacters(in: .whitespacesAndNewlines)
            if t.isEmpty || out.last == t { continue }
            out.append(t)
        }
        return out.joined(separator: "\n")
    }

    /// Wraps the first occurrence of the selection so the model can see where he is looking.
    public static func marked(_ text: String, selection: String) -> String {
        let sel = selection.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !sel.isEmpty, let r = text.range(of: sel) else { return text }
        return text.replacingCharacters(in: r, with: mark.0 + sel + mark.1)
    }

    /// At most `limit` characters: centred on the selection marker when there is one, else the top.
    public static func capped(_ text: String, limit: Int = cap) -> (text: String, cut: Bool) {
        guard text.count > limit else { return (text, false) }
        let chars = Array(text)
        guard let m = text.range(of: mark.0) else { return (String(chars[0..<limit]), true) }
        let at = text.distance(from: text.startIndex, to: m.lowerBound)
        let lo = max(0, min(chars.count - limit, at - limit / 2))
        return (String(chars[lo..<(lo + limit)]), true)
    }

    static func ocr(jpeg: Data) -> String {
        guard let src = CGImageSourceCreateWithData(jpeg as CFData, nil), let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return "" }
        return SlideWatcher.read(img)
    }
}

/// Walks one app's focused window and collects its text.
enum WindowTextReader {
    private static func attr(_ e: AXUIElement, _ k: String) -> CFTypeRef? {
        var v: CFTypeRef?
        return AXUIElementCopyAttributeValue(e, k as CFString, &v) == .success ? v : nil
    }
    private static func str(_ e: AXUIElement, _ k: String) -> String { (attr(e, k) as? String) ?? "" }

    static func read(pid: Int) throws -> (text: String, selection: String) {
        let app = AXUIElementCreateApplication(pid_t(pid))
        AXUIElementSetMessagingTimeout(app, 0.4)
        guard let w = (attr(app, kAXFocusedWindowAttribute) ?? attr(app, kAXMainWindowAttribute)), CFGetTypeID(w) == AXUIElementGetTypeID()
        else { throw HandsError.code("NO_WINDOW") }
        var pieces: [String] = []
        var nodes = 0, chars = 0
        let deadline = Date().addingTimeInterval(0.9)
        func walk(_ e: AXUIElement, _ depth: Int) {
            guard nodes < 5000, depth < 40, chars < 40_000, Date() < deadline else { return }
            nodes += 1
            let role = str(e, kAXRoleAttribute)
            if str(e, kAXSubroleAttribute) == "AXSecureTextField" { return }
            var text = ""
            switch role {
            case "AXStaticText", "AXTextArea", "AXTextField", "AXHeading", "AXLink", "AXCell":
                text = str(e, kAXValueAttribute)
                if text.isEmpty { text = str(e, kAXTitleAttribute) }
                if text.isEmpty && role != "AXTextArea" { text = str(e, kAXDescriptionAttribute) }
            default: break
            }
            if !text.isEmpty { pieces.append(text); chars += text.count }
            for c in (attr(e, kAXChildrenAttribute) as? [AXUIElement]) ?? [] { walk(c, depth + 1) }
        }
        walk(w as! AXUIElement, 0)
        var sel = ""
        if let f = attr(app, kAXFocusedUIElementAttribute), CFGetTypeID(f) == AXUIElementGetTypeID() {
            sel = str(f as! AXUIElement, kAXSelectedTextAttribute)
        }
        return (ScreenText.join(pieces), sel)
    }
}
