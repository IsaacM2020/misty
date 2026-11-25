import XCTest
@testable import MistyCore

final class PermissionsTests: XCTestCase {
    func testAutomationCodesMapToStates() {
        XCTAssertEqual(Perms.automationState(0), .granted)
        XCTAssertEqual(Perms.automationState(-1743), .denied)
        XCTAssertEqual(Perms.automationState(-1744), .notYet)
        XCTAssertEqual(Perms.automationState(-600), .notYet)      // app not running: unknown, offer to ask
    }

    func testEveryKindOpensItsOwnSettingsPane() {
        let kinds: [PermItem.Kind] = [.accessibility, .screen, .input, .fullDisk, .automation(bundle: "com.apple.Safari")]
        let urls = kinds.compactMap { Perms.paneURL($0)?.absoluteString }
        XCTAssertEqual(urls.count, 5)
        XCTAssertEqual(Set(urls).count, 5)
        XCTAssertTrue(urls[4].hasSuffix("Privacy_Automation"))
    }
}
