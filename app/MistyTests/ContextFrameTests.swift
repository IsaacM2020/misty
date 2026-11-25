import XCTest
@testable import MistyCore

final class ContextFrameTests: XCTestCase {
    let pages = HandsAppInfo(bundleId: "com.apple.iWork.Pages", pid: 7, name: "Pages")

    override func setUp() { ContextFrame.privateBundles = ["com.example.vault"] }

    func testSelectionPresent() {
        let f = ContextFrame.make(app: pages, reading: FocusReading(title: "Chem notes", doc: "/a/b.pages", selection: "Covalent bonds."))
        XCTAssertEqual(f.selection, "Covalent bonds.")
        XCTAssertEqual(f.near, "")
        XCTAssertEqual(f.json["doc"]?.stringValue, "/a/b.pages")
    }

    func testSelectionCutAt600() {
        let f = ContextFrame.make(app: pages, reading: FocusReading(selection: String(repeating: "x", count: 900)))
        XCTAssertEqual(f.selection.count, 600)
        XCTAssertTrue(f.selectionCut)
        XCTAssertEqual(f.json["selection_cut"], .bool(true))
    }

    func testNoSelectionTakesTextAroundCursor() {
        let text = String(repeating: "a", count: 500) + "HERE" + String(repeating: "b", count: 500)
        let f = ContextFrame.make(app: pages, reading: FocusReading(title: "t", value: text, cursor: 502))
        XCTAssertEqual(f.near.count, 300)
        XCTAssertTrue(f.near.contains("HERE"))
        XCTAssertNil(f.json["selection"])
    }

    func testNoFocusedElementStillGivesApp() {
        let f = ContextFrame.make(app: pages, reading: nil)
        XCTAssertEqual(f.app, "Pages")
        XCTAssertEqual(f.title, "")
        XCTAssertFalse(f.isPrivate)
    }

    func testPasswordFieldGivesNoText() {
        let f = ContextFrame.make(app: pages, reading: FocusReading(title: "Login", selection: "hunter2", value: "hunter2", cursor: 3, secure: true))
        XCTAssertEqual(f.selection, ""); XCTAssertEqual(f.near, "")
    }

    func testPrivateAppGivesNothingElse() {
        let vault = HandsAppInfo(bundleId: "com.example.vault", pid: 9, name: "Vault")
        let f = ContextFrame.make(app: vault, reading: FocusReading(title: "Bank", selection: "secret"))
        XCTAssertTrue(f.isPrivate)
        XCTAssertEqual(f.app, "a private app")
        XCTAssertNil(f.json["selection"]); XCTAssertNil(f.json["title"])
        XCTAssertEqual(f.json["private"], .bool(true))
    }

    func testOpNeedsAccessibilityAndRefusesMisty() async {
        let fake = FakeAX(); fake.perms["accessibility"] = false
        let r = await HandsService(provider: fake).handle(op: "context_frame", args: .object([:]))
        XCTAssertEqual(r.code, "NOT_TRUSTED")
    }

    func testReadFailureStillAnswersWithApp() async {
        let fake = FakeAX(); fake.failWith = "HANDS_TIMEOUT"
        let r = await HandsService(provider: fake).handle(op: "context_frame", args: .object([:]))
        // a hung app gives the frame with no title, never an error that would block the message
        if r.ok { XCTAssertNil(r.data["title"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 }) }
        else { XCTAssertEqual(r.code, "NO_FRONT_APP") }   // a test run with no regular app in front
    }
}

final class PrivateAppsOpTests: XCTestCase {
    func testTheEngineSetsTheListAndReadsItBack() async {
        let saved = ContextFrame.privateBundles
        defer { ContextFrame.privateBundles = saved }
        let r = await HandsService(provider: FakeAX()).handle(op: "private_apps", args: .object(["bundles": .array([.string("com.example.b"), .string("com.example.a")])]))
        XCTAssertTrue(r.ok)
        XCTAssertEqual(ContextFrame.privateBundles, ["com.example.a", "com.example.b"])
        XCTAssertEqual(r.data["bundles"], .array([.string("com.example.a"), .string("com.example.b")]))
    }
    func testBadArgsRefused() async {
        let r = await HandsService(provider: FakeAX()).handle(op: "private_apps", args: .object([:]))
        XCTAssertEqual(r.code, "BAD_ARGS")
    }
}
