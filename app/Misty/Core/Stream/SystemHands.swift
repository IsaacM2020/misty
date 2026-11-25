import Foundation
import AppKit
import ApplicationServices

/// The real Mac: accessibility for looking and pressing, ScreenCaptureKit for pictures, CGEvent for the keyboard and mouse.
/// Refs (`a1`, `a2`, ...) are valid until the next `axTree` for the same app.
public final class SystemHands: AXProvider, @unchecked Sendable {
    private let lock = NSLock()
    private var refs: [Int: [String: AXUIElement]] = [:]
    private var findSeq: [Int: Int] = [:]            // how many `f` refs the current snapshot has handed out, per app

    /// Every accessibility call waits at most 1.5 s (the default is 6 s): an app that is still launching or busy must not
    /// freeze a snapshot, or Misty's main thread with it.
    public init() { AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), 1.5) }

    public func permissions() -> [String: Bool] {
        ["accessibility": AXIsProcessTrusted(), "screen_recording": CGPreflightScreenCaptureAccess(),
         "post_events": CGPreflightPostEventAccess(), "listen": CGPreflightListenEventAccess()]
    }

    // MARK: apps
    private func info(_ a: NSRunningApplication) -> HandsAppInfo {
        HandsAppInfo(bundleId: a.bundleIdentifier ?? "", pid: Int(a.processIdentifier), name: a.localizedName ?? "")
    }

    public func frontApp() throws -> HandsAppInfo {
        guard let a = NSWorkspace.shared.frontmostApplication else { throw HandsError.code("NO_FRONT_APP") }
        if a.processIdentifier == getpid() {            // Misty is in front (he asked from her window): the app to act on is the one before
            let before: (bundleId: String, pid: Int, name: String)? = Thread.isMainThread
                ? MainActor.assumeIsolated { LastApp.shared.current() }
                : DispatchQueue.main.sync { MainActor.assumeIsolated { LastApp.shared.current() } }
            if let b = before { return HandsAppInfo(bundleId: b.bundleId, pid: b.pid, name: b.name) }
        }
        return info(a)
    }

    public func openApp(name: String) async throws -> HandsAppInfo {
        let ws = NSWorkspace.shared
        let want = name.lowercased()
        var app = ws.runningApplications.first { $0.activationPolicy == .regular && $0.localizedName?.lowercased() == want }
        if app == nil {
            let dirs = ["/Applications", "/System/Applications", "/System/Applications/Utilities", "/Applications/Utilities",
                        NSHomeDirectory() + "/Applications"]
            guard let path = dirs.map({ "\($0)/\(name).app" }).first(where: { FileManager.default.fileExists(atPath: $0) }) else {
                throw HandsError.code("APP_NOT_FOUND")
            }
            let cfg = NSWorkspace.OpenConfiguration()
            cfg.activates = true
            app = try await ws.openApplication(at: URL(fileURLWithPath: path), configuration: cfg)
        }
        guard let app else { throw HandsError.code("APP_NOT_FOUND") }
        app.activate(options: [.activateAllWindows])
        for _ in 0..<40 {                                                 // up to ~2 s to come to the front
            if ws.frontmostApplication?.processIdentifier == app.processIdentifier { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        try await ensureWindow(app)
        return info(app)
    }

    /// A running app can have no window at all (closed, or minimized): unminimize, else ask it to reopen, as clicking its Dock icon does.
    private func ensureWindow(_ app: NSRunningApplication) async throws {
        let pid = Int(app.processIdentifier)
        for w in windows(pid) where (attr(w, kAXMinimizedAttribute) as? Bool) == true {
            AXUIElementSetAttributeValue(w, kAXMinimizedAttribute as CFString, kCFBooleanFalse)
        }
        if !windows(pid).isEmpty { return }
        if let url = app.bundleURL {
            let cfg = NSWorkspace.OpenConfiguration()
            cfg.activates = true
            _ = try? await NSWorkspace.shared.openApplication(at: url, configuration: cfg)       // a running app gets a "reopen" and shows a window
        }
        for _ in 0..<40 {                                                  // up to ~2 s for the window to exist
            if !windows(pid).isEmpty { return }
            try await Task.sleep(for: .milliseconds(50))
        }
    }

    // MARK: accessibility
    private func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
        var out: CFTypeRef?
        return AXUIElementCopyAttributeValue(el, name as CFString, &out) == .success ? out : nil
    }

    private func string(_ el: AXUIElement, _ name: String) -> String {
        (attr(el, name) as? String) ?? ""
    }

    private func frame(_ el: AXUIElement) -> [Double] {
        var p = CGPoint.zero, s = CGSize.zero
        if let v = attr(el, kAXPositionAttribute), CFGetTypeID(v) == AXValueGetTypeID() { AXValueGetValue(v as! AXValue, .cgPoint, &p) }
        if let v = attr(el, kAXSizeAttribute), CFGetTypeID(v) == AXValueGetTypeID() { AXValueGetValue(v as! AXValue, .cgSize, &s) }
        // Hidden SwiftUI groups report inf/-inf: JSON can't carry those, and one bad frame lost the whole hands.result.
        return [Double(p.x), Double(p.y), Double(s.width), Double(s.height)].map { $0.isFinite ? $0 : 0 }
    }

    public func focusReading(pid: Int) throws -> FocusReading? { FocusReader.read(pid: pid) }
    public func windowText(pid: Int) throws -> (text: String, selection: String) { try WindowTextReader.read(pid: pid) }
    public func ocrWindow(pid: Int) async throws -> String { ScreenText.ocr(jpeg: try await Capture.windowPNG(pid: pid)) }

    public func axTree(pid: Int, maxNodes: Int) throws -> [HandsAxNode] {
        let app = AXUIElementCreateApplication(pid_t(pid))
        var roots: [AXUIElement] = []
        if let w = attr(app, kAXFocusedWindowAttribute), CFGetTypeID(w) == AXUIElementGetTypeID() { roots = [w as! AXUIElement] }
        else if let ws = attr(app, kAXWindowsAttribute) as? [AXUIElement], let first = ws.first { roots = [first] }
        guard !roots.isEmpty else { throw HandsError.code("NO_WINDOW") }
        var out: [HandsAxNode] = []
        var table: [String: AXUIElement] = [:]
        var stack: [(AXUIElement, Int)] = roots.reversed().map { ($0, 0) }
        while let (el, depth) = stack.popLast(), out.count < maxNodes {
            var role = string(el, kAXRoleAttribute)
            let secure = string(el, kAXSubroleAttribute) == "AXSecureTextField"
            if secure { role = "AXSecureTextField" }
            var label = string(el, kAXTitleAttribute)
            if label.isEmpty { label = string(el, kAXDescriptionAttribute) }
            if label.isEmpty { label = string(el, kAXPlaceholderValueAttribute) }
            if label.isEmpty { label = string(el, kAXHelpAttribute) }
            var value = ""
            if !secure, let v = attr(el, kAXValueAttribute) as? String { value = String(v.prefix(200)) }
            if role == "AXStaticText" && label.isEmpty { label = value; value = "" }
            var actions: [String] = []
            var names: CFArray?
            if AXUIElementCopyActionNames(el, &names) == .success, let ns = names as? [String] { actions = ns }
            var settable: DarwinBoolean = false
            if AXUIElementIsAttributeSettable(el, kAXValueAttribute as CFString, &settable) == .success, settable.boolValue,
               role.contains("Text") || role == "AXComboBox" { actions.append("AXSetValue") }
            let ref = "a\(out.count + 1)"
            table[ref] = el
            out.append(HandsAxNode(ref: ref, role: role, label: label, value: value, frame: frame(el), secure: secure,
                                   actions: actions, depth: depth, focused: (attr(el, kAXFocusedAttribute) as? Bool) ?? false))
            if let kids = attr(el, kAXChildrenAttribute) as? [AXUIElement] {
                for k in kids.reversed() { stack.append((k, depth + 1)) }
            }
        }
        lock.lock(); refs[pid] = table; lock.unlock()
        return out
    }

    // MARK: snapshot, search, menus (hands v2)
    private func rootWindow(_ pid: Int) throws -> AXUIElement {
        let app = AXUIElementCreateApplication(pid_t(pid))
        if let w = attr(app, kAXFocusedWindowAttribute), CFGetTypeID(w) == AXUIElementGetTypeID() { return w as! AXUIElement }
        if let ws = attr(app, kAXWindowsAttribute) as? [AXUIElement], let first = ws.first { return first }
        throw HandsError.code("NO_WINDOW")
    }

    private func menuBar(_ pid: Int) -> AXElementSource? {
        let app = AXUIElementCreateApplication(pid_t(pid))
        guard let m = attr(app, kAXMenuBarAttribute), CFGetTypeID(m) == AXUIElementGetTypeID() else { return nil }
        return AXElementSource(m as! AXUIElement)
    }

    public func axSnapshot(pid: Int, maxNodes: Int, listPreview: Int) throws -> [SnapNode] {
        let root = try rootWindow(pid)
        let picks = SnapshotWalker.walkPicks(window: AXElementSource(root), maxNodes: maxNodes, listPreview: listPreview)
        var table: [String: AXUIElement] = [:]
        for p in picks { if let src = p.source as? AXElementSource { table[p.node.ref] = src.element } }
        lock.lock(); refs[pid] = table; findSeq[pid] = 0; lock.unlock()
        return picks.map(\.node)
    }

    public func axFind(pid: Int, query: String, limit: Int) throws -> [SnapNode] {
        let menuShare = min(3, max(1, limit / 3))
        var picks: [SnapPick] = []
        if let root = try? rootWindow(pid) {
            picks = SnapshotWalker.findPicks(window: AXElementSource(root), query: query, limit: limit)
        }
        let menus = menuBar(pid).map { MenuWalker.find(menuBar: $0, query: query, limit: menuShare) } ?? []
        picks = Array(picks.prefix(max(0, limit - menus.count))) + menus
        lock.lock()
        var seq = findSeq[pid] ?? 0
        var table = refs[pid] ?? [:]
        var out: [SnapNode] = []
        for var p in picks {
            seq += 1
            p.node.ref = "f\(seq)"
            if let src = p.source as? AXElementSource { table[p.node.ref] = src.element }
            out.append(p.node)
        }
        findSeq[pid] = seq; refs[pid] = table
        lock.unlock()
        return out
    }

    public func menuList(pid: Int, path: [String]) throws -> [HandsMenuItem] {
        guard let bar = menuBar(pid) else { throw HandsError.code("NO_MENU") }
        guard let items = MenuWalker.list(menuBar: bar, path: path) else { throw HandsError.code("NO_SUCH_MENU") }
        return items.map { HandsMenuItem(title: $0.title, shortcut: $0.shortcut, enabled: $0.enabled, marked: $0.marked) }
    }

    public func menuPress(pid: Int, path: [String]) throws -> Bool {
        guard let bar = menuBar(pid) else { throw HandsError.code("NO_MENU") }
        guard let item = MenuWalker.resolve(menuBar: bar, path: path) as? AXElementSource else { throw HandsError.code("NO_SUCH_MENU") }
        return AXUIElementPerformAction(item.element, kAXPressAction as CFString) == .success
    }

    public func windowList() throws -> [HandsWindowInfo] {
        var out: [HandsWindowInfo] = []
        for app in NSWorkspace.shared.runningApplications where app.activationPolicy == .regular {
            let pid = Int(app.processIdentifier)
            for (i, w) in windows(pid).enumerated() {
                out.append(HandsWindowInfo(
                    pid: pid, bundleId: app.bundleIdentifier ?? "", app: app.localizedName ?? "", index: i,
                    title: string(w, kAXTitleAttribute), frame: frame(w),
                    minimised: (attr(w, kAXMinimizedAttribute) as? Bool) == true,
                    fullscreen: (attr(w, "AXFullScreen") as? Bool) == true))
            }
        }
        return out
    }

    public func setMinimised(pid: Int, index: Int, minimised: Bool) throws -> Bool {
        let ws = windows(pid)
        guard index >= 0, index < ws.count else { return false }
        return AXUIElementSetAttributeValue(ws[index], kAXMinimizedAttribute as CFString,
                                            minimised ? kCFBooleanTrue : kCFBooleanFalse) == .success
    }

    private func element(_ pid: Int, _ ref: String) -> AXUIElement? {
        lock.lock(); defer { lock.unlock() }
        return refs[pid]?[ref]
    }

    public func axPerform(pid: Int, ref: String, action: String) throws -> Bool {
        guard let el = element(pid, ref) else { return false }                   // a stale ref is "not done", not an error
        return AXUIElementPerformAction(el, action as CFString) == .success
    }

    public func axSetValue(pid: Int, ref: String, text: String) throws -> Bool {
        guard let el = element(pid, ref) else { return false }
        return AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFString) == .success
    }

    // MARK: pictures and input
    public func captureWindow(pid: Int) async throws -> Data { try await Capture.windowPNG(pid: pid) }
    public func pointer(x: Double, y: Double, kind: String, restore: Bool) throws { try Input.click(x: x, y: y, kind: kind, restore: restore) }
    public func probePostToPid(pid: Int, x: Double, y: Double) throws -> Bool { Input.probePostToPid(pid: pid, x: x, y: y) }
    public func keys(combo: String) throws { try Input.sendKeys(combo) }
    public func typeText(_ text: String) throws { Input.typeText(text) }

    // MARK: windows
    private func windows(_ pid: Int) -> [AXUIElement] {
        let all = (attr(AXUIElementCreateApplication(pid_t(pid)), kAXWindowsAttribute) as? [AXUIElement]) ?? []
        let traits = all.map { WindowTraits(subrole: string($0, kAXSubroleAttribute), minimisable: attr($0, kAXMinimizeButtonAttribute) != nil,
                                            minimised: (attr($0, kAXMinimizedAttribute) as? Bool) == true) }
        return RealWindows.indices(traits).map { all[$0] }
    }

    public func windowFrames(pid: Int) throws -> [[Double]] { windows(pid).map(frame) }

    public func hideApps(except: [String]) throws -> HandsHideResult {
        let keep = Set(except.map { $0.lowercased() })
        let mine = Bundle.main.bundleIdentifier
        func regular() -> [NSRunningApplication] {
            NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular && $0.bundleIdentifier != mine
                && $0.bundleIdentifier != "com.apple.finder" }
        }
        var hidden: [String] = []
        for app in regular() where !app.isHidden && !keep.contains((app.localizedName ?? "").lowercased()) {
            if app.hide() { hidden.append(app.localizedName ?? "") }
        }
        Thread.sleep(forTimeInterval: 0.4)                      // hide() is asked, not instant: read back after a moment
        let visible = regular().filter { !$0.isHidden }.map { $0.localizedName ?? "" }
        return HandsHideResult(hidden: hidden, visible: visible)
    }

    public func setWindowFrame(pid: Int, index: Int, frame f: [Double], animate: Bool) throws -> Bool {
        let ws = windows(pid)
        guard index >= 0, index < ws.count, f.count == 4 else { return false }
        func put(_ el: AXUIElement, _ r: [Double]) -> Bool {
            var p = CGPoint(x: r[0], y: r[1]), s = CGSize(width: r[2], height: r[3])
            guard let pv = AXValueCreate(.cgPoint, &p), let sv = AXValueCreate(.cgSize, &s) else { return false }
            let a = AXUIElementSetAttributeValue(el, kAXPositionAttribute as CFString, pv)
            let b = AXUIElementSetAttributeValue(el, kAXSizeAttribute as CFString, sv)
            return a == .success && b == .success
        }
        if animate {
            let from = frame(ws[index])                               // slide from where it is: about a third of a second, easing out
            if from.count == 4 {
                let steps = 12
                for i in 1..<steps {
                    let t = Double(i) / Double(steps), e = 1 - pow(1 - t, 3)
                    _ = put(ws[index], zip(from, f).map { $0 + ($1 - $0) * e })
                    usleep(28_000)
                }
            }
        }
        return put(ws[index], f)
    }

    public func screenFrame() throws -> [Double] {
        DispatchQueue.main.sync {
            guard let screen = NSScreen.screens.first else { return [0, 0, 0, 0] }
            let full = screen.frame, vis = screen.visibleFrame            // AppKit's origin is bottom left, AX's is top left
            return [Double(vis.minX), Double(full.maxY - vis.maxY), Double(vis.width), Double(vis.height)]
        }
    }
}


/// An AXUIElement as the walker reads it. All the plain attributes come back in one call (an accessibility call crosses to
/// the other app each time, so 600 nodes at a dozen calls each would be too slow); actions are read only when asked for.
final class AXElementSource: AXNodeSource {
    let element: AXUIElement
    private var loaded = false
    private var raw: [CFTypeRef?] = []
    private var kids: [AXElementSource]?
    private var acts: [String]?

    private static let names: [String] = [
        kAXRoleAttribute, kAXSubroleAttribute, kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute,
        kAXPlaceholderValueAttribute, kAXIdentifierAttribute, kAXValueAttribute, kAXPositionAttribute, kAXSizeAttribute,
        kAXFocusedAttribute, kAXSelectedAttribute, kAXEnabledAttribute, kAXMenuItemCmdCharAttribute,
        kAXMenuItemCmdModifiersAttribute]

    init(_ element: AXUIElement) { self.element = element }

    private func load() {
        if loaded { return }
        loaded = true
        raw = Array(repeating: nil, count: Self.names.count)
        var out: CFArray?
        guard AXUIElementCopyMultipleAttributeValues(element, Self.names as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &out) == .success,
              let arr = out else { return }
        for i in 0..<min(CFArrayGetCount(arr), Self.names.count) {
            guard let p = CFArrayGetValueAtIndex(arr, i) else { continue }
            let v = Unmanaged<AnyObject>.fromOpaque(p).takeUnretainedValue()
            if CFGetTypeID(v) == AXValueGetTypeID(), AXValueGetType(v as! AXValue) == .axError { continue }     // that attribute is not there
            raw[i] = v
        }
    }

    private func str(_ i: Int) -> String { load(); return (raw[i] as? String) ?? "" }
    private func flag(_ i: Int) -> Bool { load(); return (raw[i] as? Bool) ?? false }

    var role: String { str(0) }
    var subrole: String { str(1) }
    var title: String { str(2) }
    var desc: String { str(3) }
    var help: String { str(4) }
    var placeholder: String { str(5) }
    var ident: String { str(6) }
    var value: String { str(7) }
    var focused: Bool { flag(10) }
    var selected: Bool { flag(11) }
    var enabled: Bool { load(); return (raw[12] as? Bool) ?? true }

    var frame: [Double] {
        load()
        var p = CGPoint.zero, s = CGSize.zero
        if let v = raw[8], CFGetTypeID(v) == AXValueGetTypeID() { AXValueGetValue(v as! AXValue, .cgPoint, &p) }
        if let v = raw[9], CFGetTypeID(v) == AXValueGetTypeID() { AXValueGetValue(v as! AXValue, .cgSize, &s) }
        // Hidden SwiftUI groups report inf/-inf: JSON can't carry those, and one bad frame lost the whole hands.result.
        return [Double(p.x), Double(p.y), Double(s.width), Double(s.height)].map { $0.isFinite ? $0 : 0 }
    }

    /// A tick next to a menu item (the current sort order, a switched-on view option).
    var marked: Bool {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, "AXMenuItemMarkChar" as CFString, &v) == .success else { return false }
        return !((v as? String) ?? "").isEmpty
    }

    /// "Meta+Shift+N" for a menu item, in the same words the press action takes.
    var shortcut: String {
        let key = str(13)
        if key.isEmpty { return "" }
        load()
        let m = (raw[14] as? Int) ?? 0                       // bit 0 shift, 1 option, 2 control, 3 no command
        var parts: [String] = []
        if m & 8 == 0 { parts.append("Meta") }
        if m & 4 != 0 { parts.append("Control") }
        if m & 2 != 0 { parts.append("Alt") }
        if m & 1 != 0 { parts.append("Shift") }
        return (parts + [key.uppercased()]).joined(separator: "+")
    }

    var actions: [String] {
        if let a = acts { return a }
        var names: CFArray?
        var a = (AXUIElementCopyActionNames(element, &names) == .success ? (names as? [String]) : nil) ?? []
        var settable: DarwinBoolean = false
        let r = role
        if (r.contains("Text") || r == "AXComboBox"), AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable) == .success,
           settable.boolValue { a.append("AXSetValue") }
        acts = a
        return a
    }

    func children() -> [any AXNodeSource] {
        if let k = kids { return k }
        var out: CFTypeRef?
        let list = (AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &out) == .success ? out as? [AXUIElement] : nil) ?? []
        let k = list.map { AXElementSource($0) }
        kids = k
        return k
    }
}
