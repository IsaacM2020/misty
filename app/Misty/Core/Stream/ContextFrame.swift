import Foundation
import ApplicationServices

/// What the focused window of his last app holds, as read from Accessibility. Raw: the builder below decides what leaves.
public struct FocusReading: Equatable, Sendable {
    public var title: String
    public var doc: String
    public var selection: String
    public var value: String          // the focused element's text (editors, fields); empty for web areas and groups
    public var cursor: Int            // insertion point in `value`, -1 when unknown
    public var secure: Bool           // the focused element is a password field
    public var typing: Bool           // the focused element takes typed text (a text box, an editor, a terminal): dictation can go there
    public init(title: String = "", doc: String = "", selection: String = "", value: String = "", cursor: Int = -1, secure: Bool = false,
                typing: Bool = false) {
        self.title = title; self.doc = doc; self.selection = selection; self.value = value; self.cursor = cursor; self.secure = secure
        self.typing = typing
    }
}

/// The cheap always-on sense (step C, level 1): app, window, open doc, selection, a little text near the cursor.
/// Never stored by the app; the engine keeps one short line of it in history.
public struct ContextFrame: Equatable, Sendable {
    public var app: String
    public var bundleId: String
    public var title: String
    public var doc: String
    public var selection: String
    public var selectionCut: Bool
    public var near: String
    public var isPrivate: Bool
    public var at: Double
    public var typing: Bool = false   // his cursor is in a text box (never true for a password field or a private app)

    public static let selectionCap = 600
    public static let nearCap = 300

    /// Bundle ids she must not look at. The engine owns the list (setting `private_apps`, card C6) and sets it here at start
    /// and on change; this is only the fallback for before the engine has answered.
    nonisolated(unsafe) public static var privateBundles: Set<String> = [
        "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop", "com.apple.keychainaccess",
        "com.apple.Passwords", "org.keepassxc.keepassxc"]

    public static func make(app: HandsAppInfo, reading r: FocusReading?, now: Double = Date().timeIntervalSince1970) -> ContextFrame {
        if privateBundles.contains(app.bundleId) {
            return ContextFrame(app: "a private app", bundleId: "", title: "", doc: "", selection: "", selectionCut: false,
                                near: "", isPrivate: true, at: now)
        }
        guard let r else {
            return ContextFrame(app: app.name, bundleId: app.bundleId, title: "", doc: "", selection: "", selectionCut: false,
                                near: "", isPrivate: false, at: now)
        }
        let sel = r.secure ? "" : r.selection
        let cut = sel.count > selectionCap
        var near = ""
        if sel.isEmpty, !r.secure, !r.value.isEmpty, r.cursor >= 0 {
            let chars = Array(r.value)
            let c = min(r.cursor, chars.count)
            let lo = max(0, c - nearCap / 2), hi = min(chars.count, lo + nearCap)
            near = String(chars[lo..<hi])
        }
        return ContextFrame(app: app.name, bundleId: app.bundleId, title: r.title, doc: r.doc,
                            selection: String(sel.prefix(selectionCap)), selectionCut: cut, near: near, isPrivate: false, at: now,
                            typing: r.typing && !r.secure)
    }

    public var json: JSONValue {
        if isPrivate { return .object(["app": .string(app), "private": .bool(true), "at": .number(at)]) }
        var o: [String: JSONValue] = ["app": .string(app), "bundle_id": .string(bundleId), "title": .string(title),
                                      "private": .bool(false), "at": .number(at)]
        if !doc.isEmpty { o["doc"] = .string(doc) }
        if !selection.isEmpty { o["selection"] = .string(selection); if selectionCut { o["selection_cut"] = .bool(true) } }
        if !near.isEmpty { o["near"] = .string(near) }
        if typing { o["typing"] = .bool(true) }
        return .object(o)
    }
}

/// Reads the focused window and element of one app. Short timeout: a hung app must not hold up a message.
enum FocusReader {
    private static func attr(_ e: AXUIElement, _ k: String) -> CFTypeRef? {
        var v: CFTypeRef?
        return AXUIElementCopyAttributeValue(e, k as CFString, &v) == .success ? v : nil
    }
    private static func str(_ e: AXUIElement, _ k: String) -> String { (attr(e, k) as? String) ?? "" }

    static let textRoles: Set<String> = ["AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"]
    /// A text box, an editor or a terminal: its role says so, or its value can be set (web pages' rich editors, e.g. Gmail).
    static func takesText(_ el: AXUIElement, role: String) -> Bool {
        if textRoles.contains(role) { return true }
        var settable: DarwinBoolean = false
        return AXUIElementIsAttributeSettable(el, kAXValueAttribute as CFString, &settable) == .success && settable.boolValue
    }

    static func read(pid: Int) -> FocusReading? {
        let app = AXUIElementCreateApplication(pid_t(pid))
        AXUIElementSetMessagingTimeout(app, 0.4)
        var r = FocusReading()
        var any = false
        if let w = (attr(app, kAXFocusedWindowAttribute) ?? attr(app, kAXMainWindowAttribute)), CFGetTypeID(w) == AXUIElementGetTypeID() {
            let win = w as! AXUIElement
            r.title = str(win, kAXTitleAttribute)
            r.doc = str(win, kAXDocumentAttribute)
            any = true
        }
        if let f = attr(app, kAXFocusedUIElementAttribute), CFGetTypeID(f) == AXUIElementGetTypeID() {
            let el = f as! AXUIElement
            r.secure = str(el, kAXSubroleAttribute) == "AXSecureTextField"
            r.typing = !r.secure && FocusReader.takesText(el, role: str(el, kAXRoleAttribute))
            if !r.secure {
                r.selection = str(el, kAXSelectedTextAttribute)
                if r.selection.isEmpty, let v = attr(el, kAXValueAttribute) as? String {
                    r.value = v
                    if let rng = attr(el, kAXSelectedTextRangeAttribute), CFGetTypeID(rng) == AXValueGetTypeID() {
                        var cr = CFRange()
                        if AXValueGetValue(rng as! AXValue, .cfRange, &cr) { r.cursor = cr.location }
                    }
                }
            }
            any = true
        }
        return any ? r : nil
    }
}
