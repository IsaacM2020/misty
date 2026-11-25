import XCTest
@testable import MistyCore

@MainActor
final class AppStateTests: XCTestCase {
    func testStatusChangedEventUpdatesStrip() async throws {
        let state = AppState(home: NSTemporaryDirectory() + "misty-test-\(UUID().uuidString)")
        let s = try Wire.decoder().decode(Status.self, from: fixtureData("status_building"))
        await state.handle(.statusChanged(s))
        XCTAssertEqual(state.status?.build?.phase, "compile")
        XCTAssertEqual(state.strip.build, "Compiling 0 / 1")
        XCTAssertEqual(state.strip.spend, "$0.01 / $3.00")
        XCTAssertTrue(state.strip.banners.contains { $0.id == "fda-imessage" })
    }

    func testBuildProgressEventUpdatesBuildLine() async throws {
        let state = AppState(home: NSTemporaryDirectory() + "misty-test-\(UUID().uuidString)")
        let s = try Wire.decoder().decode(Status.self, from: fixtureData("status_building"))
        await state.handle(.statusChanged(s))
        var b = s.build!
        b.done = 7; b.total = 20
        await state.handle(.buildProgress(b))
        XCTAssertEqual(state.strip.build, "Compiling 7 / 20")
    }

    func testDeleteRequiresTheTypedWord() {
        XCTAssertFalse(DeleteConfirmation.isValid("delete"))
        XCTAssertFalse(DeleteConfirmation.isValid(""))
        XCTAssertTrue(DeleteConfirmation.isValid("DELETE"))
    }

    func testForceGraphChecksumMatchesPin() throws {
        let root = contractDir().deletingLastPathComponent().deletingLastPathComponent()
        let toml = try String(contentsOf: root.appendingPathComponent("config/pinned.toml"), encoding: .utf8)
        let want = toml.split(separator: "\n").first { $0.hasPrefix("sha256") }!
            .split(separator: "\"")[1]
        let js = try Data(contentsOf: root.appendingPathComponent("app/Misty/Resources/force-graph.min.js"))
        let got = sha256Hex(js)
        XCTAssertEqual(got, String(want))
    }
}
