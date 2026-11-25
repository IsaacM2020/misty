import XCTest
@testable import MistyCore

final class CursorPathTests: XCTestCase {
    func testCursorPathEndsAtTarget() {
        let p = CursorPath(from: CGPoint(x: 10, y: 20), to: CGPoint(x: 400, y: 300))
        XCTAssertEqual(p.point(at: 0).x, 10, accuracy: 1e-9)
        XCTAssertEqual(p.point(at: 0).y, 20, accuracy: 1e-9)
        XCTAssertEqual(p.point(at: 1).x, 400, accuracy: 1e-9)
        XCTAssertEqual(p.point(at: 1).y, 300, accuracy: 1e-9)
    }

    func testCurveBendsLeftOfTravel() {
        // Screen coordinates have y pointing down: travelling right, "left" is up the screen (smaller y).
        let right = CursorPath(from: CGPoint(x: 0, y: 0), to: CGPoint(x: 100, y: 0))
        XCTAssertEqual(right.point(at: 0.5).x, 50, accuracy: 1e-9)
        XCTAssertEqual(right.point(at: 0.5).y, -9, accuracy: 1e-9)        // control point 18% of 100 off the middle, curve reaches half of it
        let left = CursorPath(from: CGPoint(x: 100, y: 0), to: CGPoint(x: 0, y: 0))
        XCTAssertEqual(left.point(at: 0.5).y, 9, accuracy: 1e-9)           // the other way: left of travel is down the screen
    }

    func testHeadingFollowsTheTangent() {
        let p = CursorPath(from: CGPoint(x: 0, y: 0), to: CGPoint(x: 100, y: 0))
        XCTAssertEqual(p.heading(at: 0.5), 0, accuracy: 1e-9)             // halfway the tangent is the straight line
        XCTAssertLessThan(p.heading(at: 0), 0)                            // it starts pointing up and to the right
        XCTAssertGreaterThan(p.heading(at: 1), 0)                         // and comes in pointing down and to the right
    }

    func testDurationClamped() {
        XCTAssertEqual(CursorPath(from: .zero, to: .zero).duration, 0.35, accuracy: 1e-9)
        XCTAssertEqual(CursorPath(from: .zero, to: CGPoint(x: 480, y: 0)).duration, 0.55, accuracy: 1e-9)
        XCTAssertEqual(CursorPath(from: .zero, to: CGPoint(x: 100_000, y: 0)).duration, 0.9, accuracy: 1e-9)
    }
}

final class StreamCursorTests: XCTestCase {
    func testLocalPointOnMainDisplay() {
        let p = StreamCursor.local(CGPoint(x: 100, y: 50), screen: CGRect(x: 0, y: 0, width: 1440, height: 900), primaryHeight: 900)
        XCTAssertEqual(p, CGPoint(x: 100, y: 50))
    }

    func testLocalPointOnDisplayAboveAndRight() {
        // a second display to the right and 300 pt higher: Cocoa frame (1440, 300, 1920, 1080), so its top is AX y -480
        let p = StreamCursor.local(CGPoint(x: 1500, y: -400), screen: CGRect(x: 1440, y: 300, width: 1920, height: 1080), primaryHeight: 900)
        XCTAssertEqual(p, CGPoint(x: 60, y: 80))
    }
}
