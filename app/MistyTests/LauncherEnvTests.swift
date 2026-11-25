import XCTest
@testable import MistyCore

final class LauncherEnvTests: XCTestCase {
    func testEngineNeverWritesBytecodeAtRunTime() {
        let env = RealLauncher.environment(mistyHome: "/Users/x/Misty")
        XCTAssertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        XCTAssertNil(env["PYTHONPYCACHEPREFIX"])               // a prefix mirrors ".../Misty.app" folders into ~/Misty
        XCTAssertEqual(env["PYTHONNOUSERSITE"], "1")
        XCTAssertEqual(env["MISTY_HOME"], "/Users/x/Misty")
    }
}

final class SavedStateDecodingTests: XCTestCase {
    func testDecodesTheEngineReplyThroughWire() throws {
        let raw = #"{"openrouter_key": true, "google_email": "you@example.com"}"#   // real engine output
        let s = try Wire.decoder().decode(SavedState.self, from: Data(raw.utf8))
        XCTAssertTrue(s.openrouterKey)
        XCTAssertEqual(s.googleEmail, "you@example.com")
    }
}
