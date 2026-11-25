import Foundation

/// What the engine's `hands.request` asks of this Mac. A protocol so the dispatch can be tested with a fake that only
/// records calls (P74: Accessibility is not inherited by the engine child, so the app does the looking and the pressing).
public struct HandsAppInfo: Equatable, Sendable {
    public var bundleId: String
    public var pid: Int
    public var name: String
    public init(bundleId: String, pid: Int, name: String) { self.bundleId = bundleId; self.pid = pid; self.name = name }
}

public struct HandsAxNode: Equatable, Sendable {
    public var ref: String
    public var role: String
    public var label: String
    public var value: String
    public var frame: [Double]          // x, y, width, height in screen points, origin top left
    public var secure: Bool
    public var actions: [String]
    public var depth: Int
    public var focused: Bool
    public init(ref: String, role: String, label: String, value: String, frame: [Double], secure: Bool,
                actions: [String], depth: Int, focused: Bool) {
        self.ref = ref; self.role = role; self.label = label; self.value = value; self.frame = frame
        self.secure = secure; self.actions = actions; self.depth = depth; self.focused = focused
    }
}

public struct HandsMenuItem: Equatable, Sendable {
    public var title: String
    public var shortcut: String
    public var enabled: Bool
    public var marked: Bool          // ticked (a sort order, a view option, a checkbox item)
    public init(title: String, shortcut: String, enabled: Bool, marked: Bool = false) {
        self.title = title; self.shortcut = shortcut; self.enabled = enabled; self.marked = marked
    }
}

public struct WindowTraits: Equatable, Sendable {
    public var subrole: String
    public var minimisable: Bool
    public var minimised: Bool
    public init(subrole: String, minimisable: Bool, minimised: Bool) { self.subrole = subrole; self.minimisable = minimisable; self.minimised = minimised }
}

/// Which of an app's windows a tool may move or minimise: the ones a person would call "a window". Standard windows,
/// plus minimised windows (Finder reports those as dialogs). Helper windows (the invisible Writing Tools bar in Notes,
/// Finder's desktop) are left out, so index 0 is a real window. If that leaves nothing: any window that can minimise,
/// then every window.
public enum RealWindows {
    public static func indices(_ t: [WindowTraits]) -> [Int] {
        let real = t.indices.filter { t[$0].subrole == "AXStandardWindow" || (t[$0].minimisable && t[$0].minimised) }
        if !real.isEmpty { return real }
        let canMinimise = t.indices.filter { t[$0].minimisable }
        return canMinimise.isEmpty ? Array(t.indices) : canMinimise
    }
}

public struct HandsWindowInfo: Equatable, Sendable {
    public var pid: Int
    public var bundleId: String
    public var app: String
    public var index: Int
    public var title: String
    public var frame: [Double]
    public var minimised: Bool
    public var fullscreen: Bool
    public init(pid: Int, bundleId: String, app: String, index: Int, title: String, frame: [Double], minimised: Bool, fullscreen: Bool) {
        self.pid = pid; self.bundleId = bundleId; self.app = app; self.index = index; self.title = title
        self.frame = frame; self.minimised = minimised; self.fullscreen = fullscreen
    }
}

/// A failure the engine turns into plain words (`APP_NOT_FOUND`, `NO_WINDOW`, ...).
public enum HandsError: Error, Equatable, Sendable {
    case code(String)
}

public struct HandsHideResult: Equatable, Sendable {
    public var hidden: [String]
    public var visible: [String]
    public init(hidden: [String], visible: [String]) { self.hidden = hidden; self.visible = visible }
}

public protocol AXProvider: Sendable {
    func permissions() -> [String: Bool]
    func frontApp() throws -> HandsAppInfo
    func openApp(name: String) async throws -> HandsAppInfo
    func axTree(pid: Int, maxNodes: Int) throws -> [HandsAxNode]
    /// The priority-ordered screen map of the focused (else first) window (spec 5.1). Refs `a1`... stay valid until the next snapshot.
    func axSnapshot(pid: Int, maxNodes: Int, listPreview: Int) throws -> [SnapNode]
    /// Controls of the whole live window, plus menu items, whose name or help or value contains the query. Refs `f1`...
    func axFind(pid: Int, query: String, limit: Int) throws -> [SnapNode]
    func menuList(pid: Int, path: [String]) throws -> [HandsMenuItem]
    func menuPress(pid: Int, path: [String]) throws -> Bool
    func windowList() throws -> [HandsWindowInfo]
    func setMinimised(pid: Int, index: Int, minimised: Bool) throws -> Bool
    func axPerform(pid: Int, ref: String, action: String) throws -> Bool
    func axSetValue(pid: Int, ref: String, text: String) throws -> Bool
    func captureWindow(pid: Int) async throws -> Data
    /// `restore` puts the pointer back where it was after the click (the person's mouse is theirs).
    func pointer(x: Double, y: Double, kind: String, restore: Bool) throws
    /// Debug probe (Hands v2 Task 17): post a click straight to one process with `CGEvent.postToPid`. True if the visible pointer moved.
    func probePostToPid(pid: Int, x: Double, y: Double) throws -> Bool
    func keys(combo: String) throws
    func typeText(_ text: String) throws
    func windowFrames(pid: Int) throws -> [[Double]]
    /// D2 `apps_hide`: hide (never quit) every regular app except Misty and the named ones. Returns the names hidden and the
    /// names of regular apps still visible afterwards, so the engine can prove it.
    func hideApps(except: [String]) throws -> HandsHideResult
    /// `animate` slides the window there instead of jumping (watch mode).
    func setWindowFrame(pid: Int, index: Int, frame: [Double], animate: Bool) throws -> Bool
    func screenFrame() throws -> [Double]
    /// The focused window's title and doc and the focused element's selection (step C). Nil when there is nothing to read.
    func focusReading(pid: Int) throws -> FocusReading?
    /// The focused window's text in reading order plus the selected text (step C4). Empty text means "try OCR".
    func windowText(pid: Int) throws -> (text: String, selection: String)
    /// On-device OCR of the window (step C4). The picture exists only inside this call.
    func ocrWindow(pid: Int) async throws -> String
}

public extension AXProvider {
    func probePostToPid(pid: Int, x: Double, y: Double) throws -> Bool { throw HandsError.code("UNKNOWN_OP") }
}
