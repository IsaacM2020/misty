import XCTest
@testable import MistyCore

/// A Mac that is only a list of calls: no accessibility, no screen, no events.
final class FakeAX: AXProvider, @unchecked Sendable {
    private let lock = NSLock()
    private var _calls: [String] = []
    var calls: [String] { lock.lock(); defer { lock.unlock() }; return _calls }
    var perms: [String: Bool] = ["accessibility": true, "screen_recording": true, "post_events": true, "listen": true]
    var failWith: String?
    private func rec(_ s: String) throws {
        lock.lock(); _calls.append(s); lock.unlock()
        if let c = failWith { throw HandsError.code(c) }
    }

    func permissions() -> [String: Bool] { perms }
    func frontApp() throws -> HandsAppInfo { try rec("front_app"); return HandsAppInfo(bundleId: "com.apple.TextEdit", pid: 501, name: "TextEdit") }
    func openApp(name: String) async throws -> HandsAppInfo { try rec("open_app \(name)"); return HandsAppInfo(bundleId: "com.apple.Safari", pid: 502, name: name) }
    func axTree(pid: Int, maxNodes: Int) throws -> [HandsAxNode] {
        try rec("ax_tree \(pid) \(maxNodes)")
        return [HandsAxNode(ref: "a1", role: "AXButton", label: "Save", value: "", frame: [1, 2, 3, 4], secure: false,
                            actions: ["AXPress"], depth: 2, focused: true)]
    }
    func axSnapshot(pid: Int, maxNodes: Int, listPreview: Int) throws -> [SnapNode] {
        try rec("ax_snapshot \(pid) \(maxNodes) \(listPreview)")
        return [SnapNode(ref: "a1", role: "AXButton", subrole: "", label: "New Note", desc: "New Note", help: "", ident: "composeButton",
                         value: "", frame: [1, 2, 3, 4], secure: false, actions: ["AXPress"], depth: 2, focused: false, selected: false,
                         region: "toolbar", rows: -1, path: ["Notes"])]
    }
    func axFind(pid: Int, query: String, limit: Int) throws -> [SnapNode] {
        try rec("ax_find \(pid) \(query) \(limit)")
        return [SnapNode(ref: "f1", role: "AXMenuItem", subrole: "", label: "New Note", desc: "", help: "", ident: "", value: "",
                         frame: [0, 0, 0, 0], secure: false, actions: ["AXPress"], depth: 1, focused: false, selected: false,
                         region: "menu", rows: -1, path: ["File"])]
    }
    func menuList(pid: Int, path: [String]) throws -> [HandsMenuItem] {
        try rec("menu_list \(pid) \(path.joined(separator: ">"))")
        return [HandsMenuItem(title: "New Note", shortcut: "Meta+N", enabled: true, marked: true)]
    }
    func menuPress(pid: Int, path: [String]) throws -> Bool { try rec("menu_press \(pid) \(path.joined(separator: ">"))"); return true }
    func windowList() throws -> [HandsWindowInfo] {
        try rec("window_list")
        return [HandsWindowInfo(pid: 501, bundleId: "com.apple.TextEdit", app: "TextEdit", index: 0, title: "Untitled",
                                frame: [0, 0, 800, 600], minimised: false, fullscreen: false)]
    }
    func setMinimised(pid: Int, index: Int, minimised: Bool) throws -> Bool { try rec("set_minimised \(pid) \(index) \(minimised)"); return true }
    func axPerform(pid: Int, ref: String, action: String) throws -> Bool { try rec("ax_perform \(pid) \(ref) \(action)"); return true }
    func axSetValue(pid: Int, ref: String, text: String) throws -> Bool { try rec("ax_set_value \(pid) \(ref) \(text)"); return true }
    func captureWindow(pid: Int) async throws -> Data { try rec("capture_window \(pid)"); return Data([0x89, 0x50, 0x4E, 0x47]) }
    func pointer(x: Double, y: Double, kind: String, restore: Bool) throws { try rec("pointer \(x) \(y) \(kind)" + (restore ? " restore" : "")) }
    func probePostToPid(pid: Int, x: Double, y: Double) throws -> Bool { try rec("probe_post_to_pid \(pid) \(x) \(y)"); return false }
    func keys(combo: String) throws { try rec("keys \(combo)") }
    func typeText(_ text: String) throws { try rec("type_text \(text)") }
    func windowFrames(pid: Int) throws -> [[Double]] { try rec("window_frames \(pid)"); return [[0, 0, 800, 600]] }
    func hideApps(except: [String]) throws -> HandsHideResult { try rec("hide_apps \(except.joined(separator: ","))"); return HandsHideResult(hidden: ["Safari"], visible: except) }
    func setWindowFrame(pid: Int, index: Int, frame: [Double], animate: Bool) throws -> Bool {
        try rec("set_window_frame \(pid) \(index)" + (animate ? " animate" : "")); return true
    }
    func screenFrame() throws -> [Double] { try rec("screen_frame"); return [0, 0, 1440, 900] }
    var focus: FocusReading? = FocusReading(title: "Chem notes week 6", doc: "/School/Chem/notes-w6.pages", selection: "Covalent bonds share electrons.")
    var windowTextResult = (text: "Para one.\nPara two about bonds.", selection: "bonds")
    var ocrResult = "Text read from the picture"
    func windowText(pid: Int) throws -> (text: String, selection: String) { try rec("window_text \(pid)"); return windowTextResult }
    func ocrWindow(pid: Int) async throws -> String { try rec("ocr_window \(pid)"); return ocrResult }
    func focusReading(pid: Int) throws -> FocusReading? { try rec("focus_reading \(pid)"); return focus }
}

final class HandsServiceTests: XCTestCase {
    func run(_ ax: FakeAX, _ op: String, _ args: [String: JSONValue] = [:]) async -> HandsResult {
        await HandsService(provider: ax).handle(op: op, args: .object(args))
    }

    func testPermissionsShape() async {
        let r = await run(FakeAX(), "permissions")
        XCTAssertTrue(r.ok)
        XCTAssertEqual(r.data["accessibility"], .bool(true))
        XCTAssertEqual(Set(["accessibility", "screen_recording", "post_events", "listen"]),
                       Set(["accessibility", "screen_recording", "post_events", "listen"].filter { r.data[$0] != nil }))
    }

    func testNeverReadsMistyHerself() async {
        let ax = FakeAX()
        let r = await run(ax, "ax_snapshot", ["pid": .number(Double(getpid())), "max_nodes": .number(600)])
        XCTAssertFalse(r.ok)
        XCTAssertEqual(r.code, "IS_MISTY")
        XCTAssertTrue(ax.calls.isEmpty)
    }

    func testFrontAppUsesWireKeys() async {
        let r = await run(FakeAX(), "front_app")
        XCTAssertEqual(r.data["bundle_id"], .string("com.apple.TextEdit"))
        XCTAssertEqual(r.data["pid"], .number(501))
        XCTAssertEqual(r.data["name"], .string("TextEdit"))
    }

    func testAxTreeEncodesNodes() async {
        let ax = FakeAX()
        let r = await run(ax, "ax_tree", ["pid": .number(501), "max_nodes": .number(100)])
        XCTAssertEqual(ax.calls, ["ax_tree 501 100"])
        guard case .array(let nodes)? = r.data["nodes"], case .object(let n) = nodes[0] else { return XCTFail() }
        XCTAssertEqual(n["ref"], .string("a1"))
        XCTAssertEqual(n["frame"], .array([.number(1), .number(2), .number(3), .number(4)]))
        XCTAssertEqual(n["focused"], .bool(true))
        XCTAssertEqual(n["actions"], .array([.string("AXPress")]))
    }

    func testPerformAndSetValueReportDone() async {
        let ax = FakeAX()
        let a = await run(ax, "ax_perform", ["pid": .number(501), "ref": .string("a1"), "action": .string("AXPress")])
        let b = await run(ax, "ax_set_value", ["pid": .number(501), "ref": .string("a2"), "text": .string("hi")])
        XCTAssertEqual(a.data["done"], .bool(true))
        XCTAssertEqual(b.data["done"], .bool(true))
        XCTAssertEqual(ax.calls, ["ax_perform 501 a1 AXPress", "ax_set_value 501 a2 hi"])
    }

    func testCapturePngIsBase64() async {
        let r = await run(FakeAX(), "capture_window", ["pid": .number(501)])
        XCTAssertEqual(r.data["png"], .string(Data([0x89, 0x50, 0x4E, 0x47]).base64EncodedString()))
    }

    func testInputOpsReachTheProvider() async {
        let ax = FakeAX()
        _ = await run(ax, "pointer", ["x": .number(5), "y": .number(6), "kind": .string("double")])
        _ = await run(ax, "keys", ["combo": .string("Meta+L")])
        _ = await run(ax, "type_text", ["text": .string("hello")])
        XCTAssertEqual(ax.calls, ["pointer 5.0 6.0 double", "keys Meta+L", "type_text hello"])
    }

    func testWindowOps() async {
        let ax = FakeAX()
        let f = await run(ax, "window_frames", ["pid": .number(501)])
        XCTAssertEqual(f.data["frames"], .array([.array([.number(0), .number(0), .number(800), .number(600)])]))
        let s = await run(ax, "set_window_frame", ["pid": .number(501), "index": .number(0),
                                                    "frame": .array([.number(0), .number(0), .number(720), .number(900)])])
        XCTAssertEqual(s.data["done"], .bool(true))
        XCTAssertEqual(ax.calls.last, "set_window_frame 501 0")                         // no animation unless asked
        _ = await run(ax, "set_window_frame", ["pid": .number(501), "index": .number(0), "animate": .bool(true),
                                               "frame": .array([.number(0), .number(0), .number(720), .number(900)])])
        XCTAssertEqual(ax.calls.last, "set_window_frame 501 0 animate")                 // watch mode: the window slides
        let sc = await run(ax, "screen_frame")
        XCTAssertEqual(sc.data["frame"], .array([.number(0), .number(0), .number(1440), .number(900)]))
    }

    func testAxSnapshotEncodesRegionRowsAndPath() async {
        let ax = FakeAX()
        let r = await run(ax, "ax_snapshot", ["pid": .number(501), "max_nodes": .number(600), "list_preview": .number(5)])
        XCTAssertEqual(ax.calls, ["ax_snapshot 501 600 5"])
        guard case .array(let nodes)? = r.data["nodes"], case .object(let n) = nodes[0] else { return XCTFail() }
        XCTAssertEqual(n["label"], .string("New Note"))
        XCTAssertEqual(n["region"], .string("toolbar"))
        XCTAssertEqual(n["rows"], .number(-1))
        XCTAssertEqual(n["path"], .array([.string("Notes")]))
        XCTAssertEqual(n["ident"], .string("composeButton"))
        XCTAssertNil(n["help"])                                   // empty extras are left off the wire
    }

    func testAxSnapshotDefaultsListPreviewAndNeedsMaxNodes() async {
        let ax = FakeAX()
        _ = await run(ax, "ax_snapshot", ["pid": .number(501), "max_nodes": .number(50)])
        XCTAssertEqual(ax.calls, ["ax_snapshot 501 50 5"])
        let bad = await run(ax, "ax_snapshot", ["pid": .number(501)])
        XCTAssertEqual(bad.code, "BAD_ARGS")
    }

    func testAxFindAndMenuOps() async {
        let ax = FakeAX()
        let f = await run(ax, "ax_find", ["pid": .number(501), "query": .string("new note"), "limit": .number(8)])
        guard case .array(let nodes)? = f.data["nodes"], case .object(let n) = nodes[0] else { return XCTFail() }
        XCTAssertEqual(n["ref"], .string("f1"))
        let l = await run(ax, "menu_list", ["pid": .number(501), "path": .array([.string("File")])])
        guard case .array(let items)? = l.data["items"], case .object(let it) = items[0] else { return XCTFail() }
        XCTAssertEqual(it["shortcut"], .string("Meta+N"))
        XCTAssertEqual(it["enabled"], .bool(true))
        XCTAssertEqual(it["marked"], .bool(true))
        let p = await run(ax, "menu_press", ["pid": .number(501), "path": .array([.string("File"), .string("New Note")])])
        XCTAssertEqual(p.data["done"], .bool(true))
        XCTAssertEqual(ax.calls, ["ax_find 501 new note 8", "menu_list 501 File", "menu_press 501 File>New Note"])
        let bad = await run(ax, "menu_press", ["pid": .number(501), "path": .string("File")])
        XCTAssertEqual(bad.code, "BAD_ARGS")
    }

    func testWindowListAndMinimise() async {
        let ax = FakeAX()
        let w = await run(ax, "window_list")
        guard case .array(let rows)? = w.data["windows"], case .object(let row) = rows[0] else { return XCTFail() }
        XCTAssertEqual(row["bundle_id"], .string("com.apple.TextEdit"))
        XCTAssertEqual(row["minimised"], .bool(false))
        XCTAssertEqual(row["fullscreen"], .bool(false))
        XCTAssertEqual(row["frame"], .array([.number(0), .number(0), .number(800), .number(600)]))
        let m = await run(ax, "set_minimised", ["pid": .number(501), "index": .number(0), "minimised": .bool(true)])
        XCTAssertEqual(m.data["done"], .bool(true))
        XCTAssertEqual(ax.calls, ["window_list", "set_minimised 501 0 true"])
        let bad = await run(ax, "set_minimised", ["pid": .number(501), "index": .number(0)])
        XCTAssertEqual(bad.code, "BAD_ARGS")
    }

    func testNewAccessibilityOpsNeedTheGrant() async {
        for op in ["ax_snapshot", "ax_find", "menu_list", "menu_press", "window_list", "set_minimised"] {
            let ax = FakeAX()
            ax.perms["accessibility"] = false
            let r = await run(ax, op, ["pid": .number(1), "max_nodes": .number(5), "query": .string("x"), "path": .array([]),
                                       "index": .number(0), "minimised": .bool(true)])
            XCTAssertEqual(r.code, "NOT_TRUSTED", op)
            XCTAssertEqual(ax.calls, [], op)
        }
    }

    func testPointerRestoreFlagReachesTheProvider() async {
        let ax = FakeAX()
        _ = await run(ax, "pointer", ["x": .number(5), "y": .number(6), "kind": .string("click"), "restore": .bool(true)])
        XCTAssertEqual(ax.calls, ["pointer 5.0 6.0 click restore"])
    }

    func testProbePostToPidReachesTheProviderAndReportsIfThePointerMoved() async {
        let ax = FakeAX()
        let r = await run(ax, "probe_post_to_pid", ["pid": .number(501), "x": .number(5), "y": .number(6)])
        XCTAssertEqual(ax.calls, ["probe_post_to_pid 501 5.0 6.0"])
        XCTAssertTrue(r.ok)
        XCTAssertEqual(r.data["pointer_moved"], .bool(false))
    }

    func testAccessibilityNotTrustedStopsBeforeTheProvider() async {
        let ax = FakeAX()
        ax.perms["accessibility"] = false
        let r = await run(ax, "ax_tree", ["pid": .number(1), "max_nodes": .number(10)])
        XCTAssertFalse(r.ok)
        XCTAssertEqual(r.code, "NOT_TRUSTED")
        XCTAssertEqual(ax.calls, [])
    }

    func testCaptureNeedsScreenRecording() async {
        let ax = FakeAX()
        ax.perms["screen_recording"] = false
        let r = await run(ax, "capture_window", ["pid": .number(1)])
        XCTAssertEqual(r.code, "SCREEN_RECORDING_DENIED")
        XCTAssertEqual(ax.calls, [])
    }

    func testPointerNeedsPostEventAccess() async {
        let ax = FakeAX()
        ax.perms["post_events"] = false
        let r = await run(ax, "keys", ["combo": .string("Enter")])
        XCTAssertEqual(r.code, "NOT_TRUSTED")
        XCTAssertEqual(ax.calls, [])
    }

    func testBadArgsAndUnknownOp() async {
        let ax = FakeAX()
        let a = await run(ax, "ax_tree", [:])
        let b = await run(ax, "teleport")
        XCTAssertEqual(a.code, "BAD_ARGS")
        XCTAssertEqual(b.code, "UNKNOWN_OP")
        XCTAssertEqual(ax.calls, [])
    }

    func testHideAppsReturnsWhatWasHiddenAndWhatIsStillVisible() async {
        let ax = FakeAX()
        let r = await run(ax, "hide_apps", ["except": .array([.string("Notes")])])
        XCTAssertTrue(r.ok)
        XCTAssertEqual(r.data["hidden"], .array([.string("Safari")]))
        XCTAssertEqual(r.data["visible"], .array([.string("Notes")]))
        XCTAssertEqual(ax.calls.last, "hide_apps Notes")
    }

    func testProviderErrorBecomesItsCode() async {
        let ax = FakeAX()
        ax.failWith = "APP_NOT_FOUND"
        let r = await run(ax, "open_app", ["name": .string("Nope")])
        XCTAssertFalse(r.ok)
        XCTAssertEqual(r.code, "APP_NOT_FOUND")
    }

    func testRpcEventDecodesAHandsRequest() throws {
        let line = #"{"jsonrpc":"2.0","method":"hands.request","params":{"req_id":"rq_1","op":"front_app","args":{}}}"#
        let ev = try RpcEvent.decode(Data(line.utf8))
        XCTAssertEqual(ev, .handsRequest(reqId: "rq_1", op: "front_app", args: .object([:])))
    }

    func testBridgeAnswersTheEngineWithHandsResult() async throws {
        let api = FakeAPI()
        let bridge = HandsBridge(api: api, service: HandsService(provider: FakeAX()))
        await bridge.handle(reqId: "rq_9", op: "front_app", args: .object([:]))
        let (method, params) = try XCTUnwrap(api.calls.last)
        XCTAssertEqual(method, "hands.result")
        let o = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(params.utf8)) as? [String: Any])
        XCTAssertEqual(o["req_id"] as? String, "rq_9")
        XCTAssertEqual(o["ok"] as? Bool, true)
        XCTAssertEqual((o["data"] as? [String: Any])?["name"] as? String, "TextEdit")
    }

    func testBridgeSendsFailureCodes() async throws {
        let ax = FakeAX()
        ax.perms["accessibility"] = false
        let api = FakeAPI()
        let bridge = HandsBridge(api: api, service: HandsService(provider: ax))
        await bridge.handle(reqId: "rq_2", op: "ax_tree", args: .object(["pid": .number(1), "max_nodes": .number(5)]))
        let params = try XCTUnwrap(api.calls.last?.params)
        let o = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(params.utf8)) as? [String: Any])
        XCTAssertEqual(o["ok"] as? Bool, false)
        XCTAssertEqual(o["code"] as? String, "NOT_TRUSTED")
    }

    func testBridgeRegistersAndForwardsUserInput() async throws {
        let api = FakeAPI()
        let bridge = HandsBridge(api: api, service: HandsService(provider: FakeAX()))
        await bridge.register()
        await bridge.userInput(ts: 12.5)
        XCTAssertEqual(api.calls.map { $0.method }, ["hands.register", "hands.user_input"])
        XCTAssertTrue(api.calls[1].params.contains("12.5"))
    }
}

final class KeyComboTests: XCTestCase {
    func testParsesNamedKeysAndModifiers() {
        XCTAssertEqual(Input.parse("Enter")?.code, 36)
        XCTAssertEqual(Input.parse("Meta+L")?.code, 37)
        XCTAssertEqual(Input.parse("Meta+L")?.flags, .maskCommand)
        XCTAssertEqual(Input.parse("Shift+Tab")?.flags, .maskShift)
        XCTAssertEqual(Input.parse("Control+Alt+Left")?.flags, [.maskControl, .maskAlternate])
        XCTAssertEqual(Input.parse("PageDown")?.code, 121)
    }

    func testUnknownPartsAreRejected() {
        XCTAssertNil(Input.parse("Hyper+L"))
        XCTAssertNil(Input.parse("Meta+Banana"))
        XCTAssertNil(Input.parse(""))
    }

    func testSyntheticEventTagIsNeverZero() {
        XCTAssertNotEqual(Input.tag, 0)
    }
}

@MainActor
final class HandsRoutingTests: XCTestCase {
    func testAppStateRunsAHandsRequestThroughItsProvider() async {
        let ax = FakeAX()
        let state = AppState(home: NSTemporaryDirectory() + "misty-test-\(UUID().uuidString)", hands: ax)
        await state.handle(.handsRequest(reqId: "rq_1", op: "front_app", args: .object([:])))
        XCTAssertEqual(ax.calls, ["front_app"])
    }

    // MARK: real windows (found in the first real Gate B run)
    private func t(_ subrole: String, minimisable: Bool = true, minimised: Bool = false) -> WindowTraits {
        WindowTraits(subrole: subrole, minimisable: minimisable, minimised: minimised)
    }

    func testRealWindowsKeepOnlyStandardWindows() {
        // Notes has an invisible AXSiriUIAffordance window at index 0 that cannot move
        XCTAssertEqual(RealWindows.indices([t("AXSiriUIAffordance", minimisable: false), t("AXStandardWindow"),
                                            t("AXFloatingWindow", minimisable: false), t("AXStandardWindow")]), [1, 3])
    }

    func testAMinimisedWindowThatLostItsStandardSubroleIsStillAWindow() {
        // Finder's minimised window reports AXDialog; its untitled desktop window cannot be minimised at all
        XCTAssertEqual(RealWindows.indices([t("AXWindow", minimisable: false), t("AXDialog", minimised: true)]), [1])
    }

    func testRealWindowsFallBackToMinimisableThenToEveryWindow() {
        XCTAssertEqual(RealWindows.indices([t("AXDialog", minimisable: false), t("AXDialog")]), [1])
        XCTAssertEqual(RealWindows.indices([t("AXDialog", minimisable: false), t("", minimisable: false)]), [0, 1])
        XCTAssertEqual(RealWindows.indices([]), [])
    }
}

final class TapOwnersTests: XCTestCase {
    private final class Owner {}

    func testLookupFindsTheOwnerUntilItIsRemoved() {
        let owners = TapOwners<Owner>()
        let a = Owner(), b = Owner()
        let ia = owners.add(a), ib = owners.add(b)
        XCTAssertNotEqual(ia, ib)
        XCTAssertTrue(owners.get(UnsafeMutableRawPointer(bitPattern: ia)) === a)
        owners.remove(ia)
        XCTAssertNil(owners.get(UnsafeMutableRawPointer(bitPattern: ia)), "a callback that arrives after stop() must find nothing")
        XCTAssertTrue(owners.get(UnsafeMutableRawPointer(bitPattern: ib)) === b)
        XCTAssertNil(owners.get(nil))
    }
}
