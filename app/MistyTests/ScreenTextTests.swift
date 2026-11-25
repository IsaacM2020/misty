import XCTest
@testable import MistyCore

final class ScreenTextTests: XCTestCase {
    func testJoinDropsBlanksAndRepeats() {
        XCTAssertEqual(ScreenText.join(["A", " ", "A", "B", "\n", "A"]), "A\nB\nA")
    }
    func testSelectionIsMarked() {
        XCTAssertEqual(ScreenText.marked("one two three", selection: "two"), "one ⟦two⟧ three")
        XCTAssertEqual(ScreenText.marked("one", selection: "zzz"), "one")
        XCTAssertEqual(ScreenText.marked("one", selection: ""), "one")
    }
    func testCapKeepsTopWithoutSelection() {
        let t = String(repeating: "a", count: 9000)
        let c = ScreenText.capped(t)
        XCTAssertEqual(c.text.count, 8000); XCTAssertTrue(c.cut)
        XCTAssertFalse(ScreenText.capped("short").cut)
    }
    func testCapCentresOnTheSelection() {
        let t = String(repeating: "a", count: 20000) + "⟦HERE⟧" + String(repeating: "b", count: 20000)
        let c = ScreenText.capped(t)
        XCTAssertEqual(c.text.count, 8000); XCTAssertTrue(c.text.contains("⟦HERE⟧"))
    }
    func testOpsNeedTheirPermissions() async {
        let a = FakeAX(); a.perms["accessibility"] = false
        let r1 = await HandsService(provider: a).handle(op: "read_window_text", args: .object([:]))
        XCTAssertEqual(r1.code, "NOT_TRUSTED")
        let b = FakeAX(); b.perms["screen_recording"] = false
        let r2 = await HandsService(provider: b).handle(op: "ocr_window", args: .object([:]))
        XCTAssertEqual(r2.code, "SCREEN_RECORDING_DENIED")
    }
    func testPrivateAppIsRefusedWithoutReading() async {
        let fake = FakeAX()
        let me = await MainActor.run { LastApp.shared.current() }
        guard let me else { return }                      // no regular app in a test run: nothing to refuse
        ContextFrame.privateBundles = [me.bundleId]
        defer { ContextFrame.privateBundles = [] }
        let r = await HandsService(provider: fake).handle(op: "read_window_text", args: .object([:]))
        XCTAssertEqual(r.data["private"], .bool(true))
        XCTAssertFalse(fake.calls.contains { $0.hasPrefix("window_text") })
    }
}
