import XCTest
@testable import MistyCore

/// A scripted accessibility tree. Only what the walker reads.
final class FakeNode: AXNodeSource {
    var role = "AXGroup", subrole = "", title = "", desc = "", help = "", placeholder = "", ident = "", value = ""
    var frame: [Double] = [0, 0, 10, 10]
    var actions: [String] = []
    var focused = false, selected = false
    var enabled = true, shortcut = "", marked = false
    var kids: [FakeNode] = []
    var childReads = 0

    init(_ role: String = "AXGroup", title: String = "", desc: String = "", _ kids: [FakeNode] = []) {
        self.role = role; self.title = title; self.desc = desc; self.kids = kids
    }
    func children() -> [any AXNodeSource] { childReads += 1; return kids }
}

/// The shape of Notes (spec section 1): a split group with the sidebar and a long list first, the toolbar after it.
private func notesWindow(rows: Int = 500, selectedRow: Int? = nil) -> FakeNode {
    let list = FakeNode("AXList", title: "Notes", (0..<rows).map { i in
        let row = FakeNode("AXRow", [FakeNode("AXStaticText", title: "Note \(i)")])
        row.selected = (i == selectedRow)
        return row
    })
    let split = FakeNode("AXSplitGroup", [FakeNode("AXScrollArea", [list]), FakeNode("AXScrollArea", [FakeNode("AXTextArea")])])
    let newNote = FakeNode("AXButton", desc: "New Note")
    newNote.actions = ["AXPress"]
    let toolbar = FakeNode("AXToolbar", [newNote, FakeNode("AXButton", desc: "Delete")])
    return FakeNode("AXWindow", title: "Notes", [split, toolbar])
}

final class SnapshotWalkerTests: XCTestCase {
    func testToolbarComesBeforeAHugeList() {
        let nodes = SnapshotWalker.walk(window: notesWindow(), maxNodes: 60, listPreview: 5)
        XCTAssertLessThanOrEqual(nodes.count, 60)
        let button = nodes.first { $0.label == "New Note" }
        XCTAssertEqual(button?.region, "toolbar")
        XCTAssertEqual(button?.role, "AXButton")
        let list = nodes.first { $0.role == "AXList" }
        XCTAssertEqual(list?.rows, 500)
        let shownRows = nodes.filter { $0.role == "AXRow" }
        XCTAssertEqual(shownRows.count, 5)                       // only the preview, not the other 495
        XCTAssertEqual(nodes.first { $0.role == "AXToolbar" }?.rows, -1)
    }

    func testChildrenFollowTheirParentInReadingOrder() {
        let a = FakeNode("AXGroup", title: "A", [FakeNode("AXButton", title: "b1"), FakeNode("AXButton", title: "b2")])
        let b = FakeNode("AXGroup", title: "B", [FakeNode("AXButton", title: "b3")])
        let nodes = SnapshotWalker.walk(window: FakeNode("AXWindow", title: "W", [a, b]), maxNodes: 50, listPreview: 5)
        XCTAssertEqual(nodes.filter { $0.region == "content" }.map(\.label), ["A", "b1", "b2", "B", "b3"])
        XCTAssertEqual(nodes.filter { $0.region == "content" }.map(\.depth), [1, 2, 2, 1, 2])
    }

    func testRefsAreUniqueAndInOrder() {
        let nodes = SnapshotWalker.walk(window: notesWindow(rows: 20), maxNodes: 600, listPreview: 5)
        XCTAssertEqual(nodes.map(\.ref), (1...nodes.count).map { "a\($0)" })
    }

    func testFocusedChainIsKept() {
        let field = FakeNode("AXTextField", title: "Name")
        field.focused = true
        // 30 busy siblings come first, the focused field sits deep in the last one
        let busy = (0..<30).map { i in FakeNode("AXGroup", title: "busy \(i)", (0..<3).map { FakeNode("AXButton", title: "b\(i)-\($0)") }) }
        let last = FakeNode("AXGroup", title: "form", [FakeNode("AXGroup", title: "row", [field])])
        let window = FakeNode("AXWindow", title: "Form", busy + [last])
        let nodes = SnapshotWalker.walk(window: window, maxNodes: 12, listPreview: 5)
        let hit = nodes.first { $0.role == "AXTextField" }
        XCTAssertEqual(hit?.region, "focus")
        XCTAssertEqual(hit?.focused, true)
        XCTAssertEqual(hit?.path.suffix(2), ["form", "row"])
    }

    func testOnlyTheFocusedElementsOwnChildrenAreFocusNotItsWholeSubtree() {
        let grid = FakeNode("AXGroup", title: "Week", (0..<40).map { i in
            FakeNode("AXStaticText", title: "event \(i)", [FakeNode("AXStaticText", title: "detail \(i)")]) })
        grid.focused = true
        let nodes = SnapshotWalker.walk(window: FakeNode("AXWindow", title: "Cal", [grid]), maxNodes: 500, listPreview: 5)
        XCTAssertEqual(nodes.filter { $0.region == "focus" }.count, 41)              // the grid and its 40 direct children
        XCTAssertEqual(nodes.filter { $0.region == "content" }.count, 40)             // the details are plain content
    }

    func testSelectedRowBeyondPreviewIsKept() {
        let nodes = SnapshotWalker.walk(window: notesWindow(rows: 200, selectedRow: 150), maxNodes: 600, listPreview: 5)
        let rows = nodes.filter { $0.role == "AXRow" }
        XCTAssertEqual(rows.count, 6)
        XCTAssertTrue(rows.contains { $0.selected })
        XCTAssertTrue(nodes.contains { $0.label == "Note 150" })
    }

    func testLabelFallsBackTitleDescHelpPlaceholderIdent() {
        func label(_ n: FakeNode) -> String { SnapshotWalker.walk(window: FakeNode("AXWindow", [n]), maxNodes: 5, listPreview: 5)[1].label }
        let a = FakeNode("AXButton", title: "T", desc: "D"); a.help = "H"; a.placeholder = "P"; a.ident = "I"
        XCTAssertEqual(label(a), "T")
        let b = FakeNode("AXButton", desc: "D"); b.help = "H"; b.placeholder = "P"; b.ident = "I"
        XCTAssertEqual(label(b), "D")
        let c = FakeNode("AXButton"); c.help = "H"; c.placeholder = "P"; c.ident = "I"
        XCTAssertEqual(label(c), "H")
        let d = FakeNode("AXTextField"); d.placeholder = "P"; d.ident = "I"
        XCTAssertEqual(label(d), "P")
        let e = FakeNode("AXButton"); e.ident = "composeButton"
        XCTAssertEqual(label(e), "composeButton")
        let f = FakeNode("AXButton"); f.ident = "_NS:47"
        XCTAssertEqual(label(f), "")                              // auto-generated identifiers are noise, not names
    }

    func testStaticTextUsesItsValueAsItsName() {
        let t = FakeNode("AXStaticText"); t.value = "Hello"
        let n = SnapshotWalker.walk(window: FakeNode("AXWindow", [t]), maxNodes: 5, listPreview: 5)[1]
        XCTAssertEqual(n.label, "Hello")
        XCTAssertEqual(n.value, "")
    }

    func testSecureFieldNeverShowsItsValue() {
        let p = FakeNode("AXTextField"); p.subrole = "AXSecureTextField"; p.value = "hunter2"
        let n = SnapshotWalker.walk(window: FakeNode("AXWindow", [p]), maxNodes: 5, listPreview: 5)[1]
        XCTAssertTrue(n.secure)
        XCTAssertEqual(n.role, "AXSecureTextField")
        XCTAssertEqual(n.value, "")
    }

    func testStaticTextPrefersItsValueOverAGeneratedIdentifier() {
        let t = FakeNode("AXStaticText"); t.ident = "UnifiedTabBarButton._titleTextField"; t.value = "Start Page"
        let n = SnapshotWalker.walk(window: FakeNode("AXWindow", [t]), maxNodes: 5, listPreview: 5)[1]
        XCTAssertEqual(n.label, "Start Page")
        XCTAssertEqual(n.value, "")
    }

    func testAToolbarInsideAWebPageIsPageContentNotWindowChrome() {
        let bold = FakeNode("AXButton", title: "Bold")
        let page = FakeNode("AXWebArea", title: "Doc", [FakeNode("AXToolbar", [bold])])
        let real = FakeNode("AXToolbar", [FakeNode("AXButton", title: "Back")])
        let busy = (0..<40).map { FakeNode("AXButton", title: "x\($0)") }
        let nodes = SnapshotWalker.walk(window: FakeNode("AXWindow", title: "Safari", busy + [real, page]), maxNodes: 12, listPreview: 5)
        XCTAssertEqual(nodes.first { $0.label == "Back" }?.region, "toolbar")
        XCTAssertNil(nodes.first { $0.label == "Bold" })                  // not a priority: it only shows if the budget reaches it
        XCTAssertEqual(SnapshotWalker.walk(window: FakeNode("AXWindow", [page]), maxNodes: 12, listPreview: 5)
            .first { $0.label == "Bold" }?.region, "content")
    }

    func testSheetIsPriority() {
        let ok = FakeNode("AXButton", title: "Save")
        let sheet = FakeNode("AXSheet", [FakeNode("AXStaticText", title: "Save changes?"), ok])
        let busy = (0..<40).map { FakeNode("AXButton", title: "x\($0)") }
        let window = FakeNode("AXWindow", title: "Doc", busy + [sheet])
        let nodes = SnapshotWalker.walk(window: window, maxNodes: 10, listPreview: 5)
        XCTAssertEqual(nodes.first { $0.label == "Save" }?.region, "sheet")
    }

    func testTitleBarButtonsAreKept() {
        let close = FakeNode("AXButton"); close.subrole = "AXCloseButton"; close.desc = "close button"
        let busy = (0..<40).map { FakeNode("AXButton", title: "x\($0)") }
        let nodes = SnapshotWalker.walk(window: FakeNode("AXWindow", title: "Doc", busy + [close]), maxNodes: 6, listPreview: 5)
        XCTAssertEqual(nodes.first { $0.subrole == "AXCloseButton" }?.region, "titlebar")
    }

    func testFindMatchesDescAndIdentAcrossWholeTree() {
        let compose = FakeNode("AXButton", desc: "New Note"); compose.ident = "composeButton"
        let deep = FakeNode("AXGroup", [FakeNode("AXGroup", [compose])])
        let rows = (0..<500).map { i in FakeNode("AXRow", [FakeNode("AXStaticText", title: i == 400 ? "Groceries" : "Note \(i)")]) }
        let window = FakeNode("AXWindow", title: "Notes", [FakeNode("AXList", title: "Notes", rows), deep])
        XCTAssertEqual(SnapshotWalker.find(window: window, query: "new note", limit: 8).first?.ref, "f1")
        XCTAssertEqual(SnapshotWalker.find(window: window, query: "COMPOSE", limit: 8).first?.label, "New Note")
        let hit = SnapshotWalker.find(window: window, query: "groceries", limit: 8)
        XCTAssertEqual(hit.count, 1)                              // a row far past the list preview is still found
        XCTAssertEqual(hit.first?.path.suffix(1), ["Notes"])
        XCTAssertEqual(SnapshotWalker.find(window: window, query: "note ", limit: 3).count, 3)
        XCTAssertTrue(SnapshotWalker.find(window: window, query: "nothing like this", limit: 8).isEmpty)
    }

    func testFindMatchesValueAndPlaceholder() {
        let f = FakeNode("AXTextField"); f.placeholder = "Search"; f.value = "invoice"
        let window = FakeNode("AXWindow", [f])
        XCTAssertEqual(SnapshotWalker.find(window: window, query: "invoice", limit: 8).count, 1)
        XCTAssertEqual(SnapshotWalker.find(window: window, query: "search", limit: 8).count, 1)
    }

    func testWalkStopsAtTheScanLimitOnAHugeTree() {
        let wide = FakeNode("AXGroup", (0..<9000).map { FakeNode("AXButton", title: "b\($0)") })
        let nodes = SnapshotWalker.walk(window: FakeNode("AXWindow", [wide]), maxNodes: 600, listPreview: 5)
        XCTAssertEqual(nodes.count, 600)
    }
}

final class MenuWalkerTests: XCTestCase {
    func testMenuEntriesCarryTheirTickMark() {
        let sortBy = FakeNode("AXMenu", [FakeNode("AXMenuItem", title: "Name"), FakeNode("AXMenuItem", title: "Date Modified")])
        sortBy.kids[1].marked = true
        let bar = FakeNode("AXMenuBar", [FakeNode("AXMenuBarItem", title: "View", [FakeNode("AXMenu", [FakeNode("AXMenuItem", title: "Sort By", [sortBy])])])])
        let items = MenuWalker.list(menuBar: bar, path: ["View", "Sort By"])
        XCTAssertEqual(items?.map(\.title), ["Name", "Date Modified"])
        XCTAssertEqual(items?.map(\.marked), [false, true])
    }

    func bar() -> FakeNode {
        func item(_ t: String, key: String = "", enabled: Bool = true, sub: [FakeNode] = []) -> FakeNode {
            let n = FakeNode("AXMenuItem", title: t); n.shortcut = key; n.enabled = enabled
            if !sub.isEmpty { n.kids = [FakeNode("AXMenu", sub)] }
            return n
        }
        func top(_ t: String, _ items: [FakeNode]) -> FakeNode { let n = FakeNode("AXMenuBarItem", title: t); n.kids = [FakeNode("AXMenu", items)]; return n }
        return FakeNode("AXMenuBar", [
            top("Notes", [item("About Notes")]),
            top("File", [item("New Note", key: "Meta+N"), item("New Folder", key: "Meta+Shift+N"), item(""),
                         item("Export", sub: [item("PDF"), item("Text", enabled: false)])]),
        ])
    }

    func testTopLevelMenusWhenPathIsEmpty() {
        XCTAssertEqual(MenuWalker.list(menuBar: bar(), path: [])?.map(\.title), ["Notes", "File"])
    }

    func testItemsHaveShortcutsAndSeparatorsAreSkipped() {
        let items = MenuWalker.list(menuBar: bar(), path: ["File"])
        XCTAssertEqual(items?.map(\.title), ["New Note", "New Folder", "Export"])
        XCTAssertEqual(items?.first?.shortcut, "Meta+N")
        XCTAssertEqual(items?[1].shortcut, "Meta+Shift+N")
    }

    func testSubmenuAndMissingPath() {
        XCTAssertEqual(MenuWalker.list(menuBar: bar(), path: ["File", "Export"])?.map(\.title), ["PDF", "Text"])
        XCTAssertEqual(MenuWalker.list(menuBar: bar(), path: ["File", "Export"])?.last?.enabled, false)
        XCTAssertNil(MenuWalker.list(menuBar: bar(), path: ["Nope"]))
    }

    func testResolveIsCaseInsensitive() {
        XCTAssertNotNil(MenuWalker.resolve(menuBar: bar(), path: ["file", "new note"]))
        XCTAssertNil(MenuWalker.resolve(menuBar: bar(), path: ["File", "New Nope"]))
    }

    func testFindMenuItems() {
        let hits = MenuWalker.find(menuBar: bar(), query: "new", limit: 8)
        XCTAssertEqual(hits.map(\.node.label), ["New Note", "New Folder"])
        XCTAssertEqual(hits.first?.node.path, ["File"])
        XCTAssertEqual(hits.first?.node.region, "menu")
    }
}
