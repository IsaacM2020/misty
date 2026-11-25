import XCTest
@testable import MistyCore

final class FrameHoldTests: XCTestCase {
    func testFirstFrameWinsAndSendUsesItUp() {
        var h = FrameHold()
        XCTAssertTrue(h.wantsCapture())
        XCTAssertFalse(h.wantsCapture())                 // one capture at a time
        h.store(.object(["app": .string("Pages")]))
        XCTAssertFalse(h.wantsCapture())                 // he switched app mid-sentence: keep the first
        h.store(.object(["app": .string("Safari")]))
        XCTAssertEqual(h.take()?["app"]?.stringValue, "Pages")
        XCTAssertNil(h.take())
        XCTAssertTrue(h.wantsCapture())                  // the next utterance captures fresh
    }
    func testAFailedCaptureLeavesNothingAndCanRetry() {
        var h = FrameHold()
        XCTAssertTrue(h.wantsCapture()); h.store(nil)
        XCTAssertTrue(h.wantsCapture())
    }
}

final class ActivityGateTests: XCTestCase {
    func testChangeAtOnceThenFiveSecondFloorThenHeartbeat() {
        var g = ActivityGate()
        let t = Date(timeIntervalSince1970: 1000)
        XCTAssertTrue(g.shouldSend(key: "a", now: t))
        XCTAssertFalse(g.shouldSend(key: "b", now: t.addingTimeInterval(2)))       // too soon: held for the next tick
        XCTAssertTrue(g.shouldSend(key: "b", now: t.addingTimeInterval(6)))
        XCTAssertFalse(g.shouldSend(key: "b", now: t.addingTimeInterval(30)))       // nothing changed
        XCTAssertTrue(g.shouldSend(key: "b", now: t.addingTimeInterval(70)))        // heartbeat
    }
}
