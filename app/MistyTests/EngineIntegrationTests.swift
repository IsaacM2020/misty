import XCTest
@testable import MistyCore

/// Real Swift <-> real Python: launches the engine as a child process and talks to it over the socket.
/// Runs only when MISTY_ENGINE_CMD is set (scripts/swift_test.sh sets it).
final class EngineIntegrationTests: XCTestCase {
    func testSupervisorLaunchesEngineAndRpcWorksEndToEnd() async throws {
        guard ProcessInfo.processInfo.environment["MISTY_ENGINE_CMD"] != nil else {
            throw XCTSkip("MISTY_ENGINE_CMD not set")
        }
        let home = "/tmp/mi-\(UUID().uuidString.prefix(8))"
        let sock = home + "/run/engine.sock"
        try FileManager.default.createDirectory(atPath: home + "/run", withIntermediateDirectories: true)
        let launcher = RealLauncher(socketPath: sock, mistyHome: home, helpersDir: nil)
        let sup = EngineSupervisor(launcher: launcher)
        await sup.start()
        // Stop the engine, then delete the throwaway home. The engine downloads ~1.4 GB of models
        // into it, and leaving it behind filled the disk (2026-11-15: 60+ dirs, ~90 GB in /tmp).
        addTeardownBlock {
            await sup.stop()
            try? FileManager.default.removeItem(atPath: home)
        }

        var waited = 0
        while !FileManager.default.fileExists(atPath: sock) && waited < 150 {
            try await Task.sleep(for: .milliseconds(200)); waited += 1
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: sock), "engine socket never appeared")

        let c = RpcClient(socketPath: URL(fileURLWithPath: sock))
        try await c.connect()
        let st: Status = try await c.call("status.get", Empty())
        XCTAssertTrue(st.online)
        XCTAssertEqual(st.spend.capDaily, 0.2, accuracy: 1e-9)

        // a mutation round trip: pause, then see it in status
        let _: JSONValue = try await c.call("control.pause", Empty())
        let st2: Status = try await c.call("status.get", Empty())
        XCTAssertTrue(st2.paused)

        // an engine error comes back as a code
        do {
            let _: JSONValue = try await c.call("setup.start_build", ["limit": 5])
            XCTFail("expected PAID_LOCKED")
        } catch { XCTAssertEqual(error as? RpcError, .server(code: "PAID_LOCKED")) }

        // the contract shapes decode from the real engine, not only from fixtures
        let g: GraphData = try await c.call("graph.get", Empty())
        XCTAssertEqual(g.nodes.first?.id, "me")
        let sg: SuggestionList = try await c.call("suggestions.list", ["status": "new"])
        XCTAssertEqual(sg.suggestions.count, 0)
        let pr: PrioritiesState = try await c.call("priorities.get", Empty())
        XCTAssertEqual(pr.status, "draft")
        await c.disconnect()
    }
}
