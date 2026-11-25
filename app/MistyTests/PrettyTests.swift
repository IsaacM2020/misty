import XCTest
@testable import MistyCore

final class PrettyTests: XCTestCase {
    func testDatesAndIntervalsReadLikeAPersonWroteThem() {
        XCTAssertEqual(Pretty.day("2026-11-03"), "3 Nov")
        XCTAssertEqual(Pretty.day("2026-12-25T09:00:00"), "25 Dec")
        XCTAssertEqual(Pretty.day("Friday"), "Friday")
        XCTAssertEqual(Pretty.every(15), "every 15 min")
        XCTAssertEqual(Pretty.every(180), "every 3 h")
        XCTAssertEqual(Pretty.every(90), "every 90 min")
    }

    func testTheSpokenFillerIsNotKeptAboveTheRealReply() {
        XCTAssertEqual(Pretty.reply("One sec.\nI'm watching the match."), "I'm watching the match.")
        XCTAssertEqual(Pretty.reply("One sec."), "One sec.")                       // still working: nothing else to show yet
        XCTAssertEqual(Pretty.reply("  Opened your essay.\n"), "Opened your essay.")
    }
}
