import XCTest
@testable import MistyCore


/// Captions read aloud while the picture builds: one step at a time, the reply held until the last caption, a tap
/// ends it, and a caption written for the eye is turned into words.
@MainActor
final class NarratorTests: XCTestCase {
    final class Log { var events: [String] = [] }

    func fast() -> Narrator { let n = Narrator(); n.breath = 0.01; n.silentStep = 0.02; return n }

    func wait(_ ms: Int = 60) async { try? await Task.sleep(for: .milliseconds(ms)) }

    func testShowsAStepThenSaysItThenWaitsForTheSpeechBeforeTheNext() async {
        let n = fast(), log = Log()
        var rest: [String]?
        n.run(["one", "two"], show: { log.events.append("show \($0)") }, speak: { log.events.append("say \($0)"); return true },
              done: { rest = $0 })
        await wait()
        XCTAssertEqual(log.events, ["show 0", "say one"])         // waiting for her to finish the first caption
        n.spoke()
        await wait()
        XCTAssertEqual(log.events, ["show 0", "say one", "show 1", "say two"])
        XCTAssertNil(rest)
        n.spoke()
        await wait()
        XCTAssertEqual(rest, [])
        XCTAssertFalse(n.active)
    }

    func testTheReplyIsHeldDuringTheCaptionsAndHandedBackAfter() async {
        let n = fast()
        var rest: [String]?
        XCTAssertFalse(n.hold("not yet active"))
        n.run(["a"], show: { _ in }, speak: { _ in true }, done: { rest = $0 })
        XCTAssertTrue(n.hold("The graph has two branches."))
        XCTAssertTrue(n.hold("Both avoid x equals 2."))
        await wait()
        n.spoke()
        await wait()
        XCTAssertEqual(rest, ["The graph has two branches.", "Both avoid x equals 2."])
        XCTAssertFalse(n.hold("later"))                             // after it ends the reply speaks normally
    }

    func testATapCancelsAtOnceAndDropsTheHeldReply() async {
        let n = fast(), log = Log()
        var rest: [String]?
        n.run(["one", "two"], show: { log.events.append("show \($0)") }, speak: { _ in true }, done: { rest = $0 })
        _ = n.hold("reply")
        await wait()
        n.cancel()
        await wait()
        XCTAssertEqual(log.events, ["show 0"])                      // never reaches step two
        XCTAssertNil(rest)
        XCTAssertFalse(n.active)
    }

    func testWithNoVoiceItStillWalksThroughTheStepsOnATimer() async {
        let n = fast(), log = Log()
        var rest: [String]?
        n.run(["one", "", "three"], show: { log.events.append("show \($0)") }, speak: { _ in false }, done: { rest = $0 })
        await wait(200)
        XCTAssertEqual(log.events, ["show 0", "show 1", "show 2"])
        XCTAssertEqual(rest, [])
    }

    func testANewRunReplacesTheOldOne() async {
        let n = fast(), log = Log()
        n.run(["old1", "old2"], show: { log.events.append("old \($0)") }, speak: { _ in true }, done: { _ in })
        await wait()
        n.run(["new1"], show: { log.events.append("new \($0)") }, speak: { _ in true }, done: { _ in })
        await wait()
        n.spoke()
        await wait()
        XCTAssertEqual(log.events, ["old 0", "new 0"])
    }

    func testCaptionsBecomeSpeakableWords() {
        XCTAssertEqual(Speakable.text("3x^2 + 2 = 14"), "3x squared plus 2 equals 14")
        XCTAssertEqual(Speakable.text("f(x) = 3x/(x-2)"), "f of x equals 3x over x minus 2")
        XCTAssertEqual(Speakable.text("x = -2"), "x equals negative 2")
        XCTAssertEqual(Speakable.text("The curve never touches x = 2"), "The curve never touches x equals 2")
        XCTAssertEqual(Speakable.text("  "), "")
        XCTAssertEqual(Speakable.text("x² ≤ 4"), "x squared is at most 4")
    }
}
