import Foundation

/// One accessibility element, as the walker needs it. The real one wraps an AXUIElement (SystemHands.swift); the tests
/// use a scripted tree. A protocol so the walk order, the list collapsing and the search are pure and testable.
public protocol AXNodeSource {
    var role: String { get }
    var subrole: String { get }
    var title: String { get }
    var desc: String { get }
    var help: String { get }
    var placeholder: String { get }
    var ident: String { get }
    var value: String { get }
    var frame: [Double] { get }
    var actions: [String] { get }
    var focused: Bool { get }
    var selected: Bool { get }
    var enabled: Bool { get }
    var shortcut: String { get }
    var marked: Bool { get }
    func children() -> [any AXNodeSource]
}

public extension AXNodeSource {
    var enabled: Bool { true }
    var shortcut: String { "" }
    var marked: Bool { false }
}

/// A control as the engine sees it (hands v2 spec 5.1). `region` says why it was kept: a toolbar button, a sheet control,
/// the focused chain, a title bar button or plain content. `rows` is the full child count of a collapsed list, else -1.
public struct SnapNode: Equatable, Sendable {
    public var ref: String
    public var role: String
    public var subrole: String
    public var label: String
    public var desc: String
    public var help: String
    public var ident: String
    public var value: String
    public var frame: [Double]
    public var secure: Bool
    public var actions: [String]
    public var depth: Int
    public var focused: Bool
    public var selected: Bool
    public var region: String
    public var rows: Int
    public var path: [String]
}

public struct SnapPick {
    public var node: SnapNode
    public var source: any AXNodeSource
}

public enum SnapshotWalker {
    static let listRoles: Set<String> = ["AXList", "AXTable", "AXOutline", "AXBrowser"]
    static let titleButtons: Set<String> = ["AXCloseButton", "AXMinimizeButton", "AXZoomButton", "AXFullScreenButton"]
    static let sheetRoles: Set<String> = ["AXSheet", "AXPopover", "AXDrawer"]
    static let sheetSubroles: Set<String> = ["AXDialog", "AXSystemDialog"]
    /// How many elements a scan or a search looks at before giving up, and how many list rows it checks for a selection.
    static let scanLimit = 2500
    /// A PDF in Preview or a SwiftUI app like Weather can hold thousands of nodes at a cross-process call each: return what
    /// was read after this long instead of timing out (Stream, 2026-10-21).
    static let scanSeconds = 2.0
    static let rowScanLimit = 1000
    static let pathDepth = 4

    // MARK: naming
    /// What a control calls itself: title, description, help, placeholder.
    static func baseName(_ s: any AXNodeSource) -> String {
        for c in [s.title, s.desc, s.help, s.placeholder] where !c.isEmpty { return c }
        return ""
    }

    /// What to call a control, last resort its identifier. Generated identifiers (`_NS:12`) are noise.
    static func name(_ s: any AXNodeSource) -> String {
        let b = baseName(s)
        if !b.isEmpty { return b }
        return s.ident.hasPrefix("_NS:") ? "" : s.ident
    }

    static func node(_ s: any AXNodeSource, ref: String, depth: Int, region: String, rows: Int, path: [String]) -> SnapNode {
        let secure = s.subrole == "AXSecureTextField"
        var label = baseName(s)
        var value = secure ? "" : String(s.value.prefix(200))
        if label.isEmpty && s.role == "AXStaticText" && !value.isEmpty { label = value; value = "" }      // the text is its name
        if label.isEmpty { label = name(s) }
        return SnapNode(ref: ref, role: secure ? "AXSecureTextField" : s.role, subrole: s.subrole, label: label, desc: s.desc,
                        help: s.help, ident: s.ident, value: value, frame: s.frame, secure: secure, actions: s.actions,
                        depth: depth, focused: s.focused, selected: s.selected, region: region, rows: rows, path: path)
    }

    // MARK: the scan tree
    private final class Scan {
        let src: any AXNodeSource
        let parent: Scan?
        let depth: Int
        var kids: [Scan] = []
        var region = "content"
        var assigned = false
        var emitted = false
        var order = 0                    // position in a depth-first walk: the order a person reads the window in
        var rows = -1
        init(_ src: any AXNodeSource, parent: Scan?, depth: Int) { self.src = src; self.parent = parent; self.depth = depth }
    }

    private static func build(_ root: any AXNodeSource, listPreview: Int) -> Scan {
        let top = Scan(root, parent: nil, depth: 0)
        var seen = 1
        var queue: [Scan] = [top]
        var head = 0
        let deadline = Date().addingTimeInterval(scanSeconds)
        while head < queue.count, seen < scanLimit, Date() < deadline {
            let n = queue[head]; head += 1
            let all = n.src.children()
            var chosen: [any AXNodeSource]
            if listRoles.contains(n.src.role) {
                n.rows = all.count
                var keep = Set(0..<min(listPreview, all.count))
                for (i, k) in all.enumerated() where i < rowScanLimit && !keep.contains(i) && (k.selected || k.focused) { keep.insert(i) }
                chosen = keep.sorted().map { all[$0] }
            } else {
                chosen = all
            }
            for c in chosen where seen < scanLimit {
                let s = Scan(c, parent: n, depth: n.depth + 1)
                n.kids.append(s)
                queue.append(s)
                seen += 1
            }
        }
        return top
    }

    private static func mark(_ n: Scan, _ region: String) {
        var stack = [n]
        while let x = stack.popLast() {
            if x.assigned { continue }
            x.assigned = true
            x.region = region
            stack.append(contentsOf: x.kids)
        }
    }

    /// A toolbar or sheet inside a web page is page content (a Google Docs ribbon), not the window's own chrome.
    private static func insideWebPage(_ n: Scan) -> Bool {
        var p = n.parent
        while let x = p { if x.src.role == "AXWebArea" { return true }; p = x.parent }
        return false
    }

    private static func number(_ root: Scan) {
        var stack = [root]
        var i = 0
        while let n = stack.popLast() {
            n.order = i; i += 1
            stack.append(contentsOf: n.kids.reversed())
        }
    }

    private static let regionRank: [String: Int] = ["toolbar": 0, "sheet": 1, "focus": 2, "titlebar": 3, "content": 4]

    private static func allNodes(_ root: Scan) -> [Scan] {
        var out: [Scan] = [root]
        var head = 0
        while head < out.count { out.append(contentsOf: out[head].kids); head += 1 }
        return out
    }

    private static func path(of n: Scan) -> [String] {
        var out: [String] = []
        var p = n.parent
        while let x = p, out.count < pathDepth {
            let l = name(x.src)
            if !l.isEmpty { out.append(l) }
            p = x.parent
        }
        return out.reversed()
    }

    // MARK: walking
    /// The window as a priority-ordered list (spec 5.1): the title bar buttons, every toolbar, every sheet or popover and
    /// the focused chain come first, then everything else breadth-first. Long lists show `listPreview` rows plus the
    /// selected or focused one. Stops at `maxNodes`.
    public static func walkPicks(window: any AXNodeSource, maxNodes: Int, listPreview: Int) -> [SnapPick] {
        let top = build(window, listPreview: listPreview)
        let nodes = allNodes(top)
        number(top)
        top.assigned = true
        top.region = "titlebar"
        var priority: [Scan] = []
        for k in top.kids where titleButtons.contains(k.src.subrole) { mark(k, "titlebar"); priority.append(k) }
        for n in nodes where n.src.role == "AXToolbar" && !n.assigned && !insideWebPage(n) { mark(n, "toolbar"); priority.append(n) }
        for n in nodes where (sheetRoles.contains(n.src.role) || sheetSubroles.contains(n.src.subrole)) && !n.assigned
            && !insideWebPage(n) {
            mark(n, "sheet"); priority.append(n)
        }
        var chain: [Scan] = []
        var single: [Scan] = []                          // priority nodes that stand alone (not their whole subtree)
        if let f = nodes.first(where: { $0.src.focused && $0.parent != nil }) {
            var p = f.parent
            while let x = p, x.parent != nil { if !x.assigned { x.assigned = true; x.region = "focus" }; chain.append(x); p = x.parent }
            chain.reverse()
            if !f.assigned { f.assigned = true; f.region = "focus" }
            for k in f.kids where !k.assigned { k.assigned = true; k.region = "focus" }         // its own children, not the whole subtree
            single = chain + [f] + f.kids
        }
        priority.append(contentsOf: single)

        var chosen: [Scan] = []
        func emit(_ n: Scan) {
            if n.emitted || chosen.count >= maxNodes { return }
            n.emitted = true
            chosen.append(n)
        }
        func bfs(from start: Scan) {
            var q: [Scan] = [start]
            var h = 0
            while h < q.count, chosen.count < maxNodes {
                let n = q[h]; h += 1
                emit(n)
                q.append(contentsOf: n.kids)
            }
        }
        emit(top)
        for p in priority {
            if single.contains(where: { $0 === p }) { emit(p) } else { bfs(from: p) }     // chain and focus children stand alone
            if chosen.count >= maxNodes { break }
        }
        var q: [Scan] = [top]
        var h = 0
        while h < q.count, chosen.count < maxNodes {
            let n = q[h]; h += 1
            emit(n)
            q.append(contentsOf: n.kids)
        }
        // Chosen by priority and breadth, delivered in reading order inside each region, so a parent is followed by its children.
        chosen.sort { (regionRank[$0.region] ?? 4, $0.order) < (regionRank[$1.region] ?? 4, $1.order) }
        return chosen.enumerated().map { i, n in
            SnapPick(node: node(n.src, ref: "a\(i + 1)", depth: n.depth, region: n.region, rows: n.rows, path: path(of: n)), source: n.src)
        }
    }

    public static func walk(window: any AXNodeSource, maxNodes: Int, listPreview: Int) -> [SnapNode] {
        walkPicks(window: window, maxNodes: maxNodes, listPreview: listPreview).map(\.node)
    }

    // MARK: searching
    static func matches(_ s: any AXNodeSource, _ q: String) -> Bool {
        [s.title, s.desc, s.help, s.placeholder, s.ident, s.value].contains { $0.lowercased().contains(q) }
    }

    /// Every element of the live window (up to 5000), lists included, whose name, help, placeholder, identifier or value
    /// contains the query. Refs are `f1`, `f2`, ... so they never collide with a snapshot's `a` refs.
    public static func findPicks(window: any AXNodeSource, query: String, limit: Int, refPrefix: String = "f", start: Int = 1) -> [SnapPick] {
        let q = query.lowercased().trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty, limit > 0 else { return [] }
        var out: [SnapPick] = []
        var seen = 1
        var queue: [Scan] = [Scan(window, parent: nil, depth: 0)]
        var head = 0
        while head < queue.count, out.count < limit {
            let n = queue[head]; head += 1
            if n.parent != nil, matches(n.src, q) {
                out.append(SnapPick(node: node(n.src, ref: "\(refPrefix)\(start + out.count)", depth: n.depth, region: "content",
                                               rows: -1, path: path(of: n)), source: n.src))
            }
            if seen >= scanLimit { continue }
            for c in n.src.children() where seen < scanLimit {
                queue.append(Scan(c, parent: n, depth: n.depth + 1))
                seen += 1
            }
        }
        return out
    }

    public static func find(window: any AXNodeSource, query: String, limit: Int) -> [SnapNode] {
        findPicks(window: window, query: query, limit: limit).map(\.node)
    }
}

/// Menu bar helpers: top-level menus, the items of one menu, and finding an item by its path. Pure over AXNodeSource.
public struct MenuEntry {
    public var title: String
    public var shortcut: String
    public var enabled: Bool
    public var marked: Bool
    public var source: any AXNodeSource
}

public enum MenuWalker {
    /// The items one level down: for a bar or a menu its children; for an item or bar item, the children of its AXMenu.
    static func level(_ s: any AXNodeSource) -> [any AXNodeSource] {
        let kids = s.children()
        if s.role == "AXMenuBar" || s.role == "AXMenu" { return kids }
        return kids.first { $0.role == "AXMenu" }?.children() ?? []
    }

    static func entries(_ s: any AXNodeSource) -> [MenuEntry] {
        level(s).compactMap { c in
            let t = SnapshotWalker.name(c)
            return t.isEmpty ? nil : MenuEntry(title: t, shortcut: c.shortcut, enabled: c.enabled, marked: c.marked, source: c)
        }
    }

    public static func resolve(menuBar: any AXNodeSource, path: [String]) -> (any AXNodeSource)? {
        var cur: any AXNodeSource = menuBar
        for step in path {
            guard let next = entries(cur).first(where: { $0.title.lowercased() == step.lowercased() }) else { return nil }
            cur = next.source
        }
        return cur
    }

    /// nil when the path does not exist.
    public static func list(menuBar: any AXNodeSource, path: [String]) -> [MenuEntry]? {
        resolve(menuBar: menuBar, path: path).map(entries)
    }

    /// Enabled items whose title contains the query, with the menu they sit in as the path.
    public static func find(menuBar: any AXNodeSource, query: String, limit: Int, refPrefix: String = "f", start: Int = 1) -> [SnapPick] {
        let q = query.lowercased().trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty, limit > 0 else { return [] }
        var out: [SnapPick] = []
        func visit(_ s: any AXNodeSource, _ trail: [String], depth: Int) {
            for e in entries(s) where out.count < limit {
                if e.enabled, e.title.lowercased().contains(q), !trail.isEmpty {
                    var n = SnapshotWalker.node(e.source, ref: "\(refPrefix)\(start + out.count)", depth: depth, region: "menu",
                                                rows: -1, path: trail)
                    n.label = e.title
                    n.actions = ["AXPress"]
                    out.append(SnapPick(node: n, source: e.source))
                }
                if depth < 4 { visit(e.source, trail + [e.title], depth: depth + 1) }
            }
        }
        visit(menuBar, [], depth: 1)
        return out
    }
}
