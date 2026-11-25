import XCTest
@testable import MistyCore

/// V4: the experimental LFM3 voice toggle. The process side is a state machine over a fake child; the wire side is pure.
final class FakeLFMChild: LFMChild, @unchecked Sendable {
    private let lock = NSLock()
    private var _running = true, _terminated = 0, _killed = 0
    private var exitHandler: (@Sendable () -> Void)?
    let ignoresTerminate: Bool
    init(ignoresTerminate: Bool = false) { self.ignoresTerminate = ignoresTerminate }
    var isRunning: Bool { lock.withLock { _running } }
    var terminated: Int { lock.withLock { _terminated } }
    var killed: Int { lock.withLock { _killed } }
    func terminate() { lock.withLock { _terminated += 1; if !ignoresTerminate { _running = false } }; if !ignoresTerminate { exitHandler?() } }
    func kill() { lock.withLock { _killed += 1; _running = false }; exitHandler?() }
    func onExit(_ f: @escaping @Sendable () -> Void) { exitHandler = f }
    func dieByItself() { lock.withLock { _running = false }; exitHandler?() }
}

final class FakeLFMLauncher: LFMLauncher, @unchecked Sendable {
    private let lock = NSLock()
    var children: [FakeLFMChild] = []
    var launches = 0, reaps = 0
    var healthy = true
    var launchError: Error?
    var nextIgnoresTerminate = false
    var ports = [51001, 51002, 51003]
    func freePort() -> Int { lock.withLock { ports.isEmpty ? 51999 : ports.removeFirst() } }
    func launch(port: Int) throws -> LFMChild {
        if let e = launchError { throw e }
        let c = FakeLFMChild(ignoresTerminate: nextIgnoresTerminate)
        lock.withLock { launches += 1; children.append(c) }
        return c
    }
    func isHealthy(port: Int) async -> Bool { lock.withLock { healthy } }
    func reapOrphans() { lock.withLock { reaps += 1 } }
    var last: FakeLFMChild? { lock.withLock { children.last } }
    func setHealthy(_ v: Bool) { lock.withLock { healthy = v } }
}

struct Boom: Error, LocalizedError { var errorDescription: String? { "no llama here" } }

@MainActor
final class LFMHostTests: XCTestCase {
    func host(_ l: FakeLFMLauncher, timeout: Duration = .seconds(5), grace: Duration = .milliseconds(30)) -> LFMHost {
        LFMHost(launcher: l, readyTimeout: timeout, poll: .milliseconds(5), graceKill: grace, observeQuit: false)
    }
    func until(_ ok: @escaping @MainActor () -> Bool, _ secs: Double = 2) async {
        let end = Date().addingTimeInterval(secs)
        while !ok() && Date() < end { try? await Task.sleep(for: .milliseconds(5)) }
    }

    func testItStartsOffAndNeverRoutes() {
        let h = host(FakeLFMLauncher())
        XCTAssertEqual(h.state, .off)
        XCTAssertFalse(h.canRoute(classMode: false))
        XCTAssertNil(h.port)
    }

    func testEnableStartsOneChildOnAFreePortAndBecomesReady() async {
        let l = FakeLFMLauncher(); let h = host(l)
        h.enable(classMode: false)
        XCTAssertEqual(h.state, .starting)
        XCTAssertFalse(h.canRoute(classMode: false), "not ready yet: push-to-talk stays normal EDITH")
        await until { h.state == .ready }
        XCTAssertEqual(h.state, .ready)
        XCTAssertEqual(h.port, 51001)
        XCTAssertEqual(l.launches, 1)
        XCTAssertTrue(h.canRoute(classMode: false))
    }

    func testItNeverRoutesInClassMode() async {
        let l = FakeLFMLauncher(); let h = host(l)
        h.enable(classMode: false)
        await until { h.state == .ready }
        XCTAssertFalse(h.canRoute(classMode: true))
    }

    func testEnableInClassModeDoesNothing() {
        let l = FakeLFMLauncher(); let h = host(l)
        h.enable(classMode: true)
        XCTAssertEqual(h.state, .off)
        XCTAssertEqual(l.launches, 0)
    }

    func testEnableTwiceStartsOnlyOneChild() async {
        let l = FakeLFMLauncher(); let h = host(l)
        h.enable(classMode: false); h.enable(classMode: false)
        await until { h.state == .ready }
        h.enable(classMode: false)
        XCTAssertEqual(l.launches, 1)
    }

    func testDisableKillsTheChildAndGoesBackToNormal() async {
        let l = FakeLFMLauncher(); let h = host(l)
        h.enable(classMode: false)
        await until { h.state == .ready }
        h.disable()
        XCTAssertEqual(h.state, .off)
        XCTAssertNil(h.port)
        XCTAssertEqual(l.last?.terminated, 1)
        XCTAssertFalse(l.last?.isRunning ?? true)
        XCTAssertFalse(h.canRoute(classMode: false))
    }

    func testAChildThatIgnoresTheStopSignalIsKilledAfterAShortGrace() async {
        let l = FakeLFMLauncher(); l.nextIgnoresTerminate = true; let h = host(l)
        h.enable(classMode: false)
        await until { h.state == .ready }
        h.disable()
        XCTAssertTrue(l.last?.isRunning ?? false, "still running right after the polite stop")
        await until { l.last?.isRunning == false }
        XCTAssertEqual(l.last?.killed, 1)
        XCTAssertFalse(l.last?.isRunning ?? true)
    }

    func testAChildThatDiesByItselfIsAFailureNotASilentHang() async {
        let l = FakeLFMLauncher(); let h = host(l)
        h.enable(classMode: false)
        await until { h.state == .ready }
        l.last?.dieByItself()
        await until { if case .failed = h.state { return true } else { return false } }
        guard case .failed = h.state else { return XCTFail("expected failed, got \(h.state)") }
        XCTAssertFalse(h.canRoute(classMode: false))
    }

    func testALaunchErrorIsShownAndLeavesNothingRunning() {
        let l = FakeLFMLauncher(); l.launchError = Boom(); let h = host(l)
        h.enable(classMode: false)
        XCTAssertEqual(h.state, .failed("no llama here"))
        XCTAssertEqual(l.launches, 0)
    }

    func testNeverHealthyTimesOutAndStopsTheChild() async {
        let l = FakeLFMLauncher(); l.setHealthy(false)
        let h = host(l, timeout: .milliseconds(80))
        h.enable(classMode: false)
        await until { if case .failed = h.state { return true } else { return false } }
        guard case .failed = h.state else { return XCTFail("expected failed, got \(h.state)") }
        XCTAssertEqual(l.last?.terminated, 1)
    }

    func testDisableWhileStartingStaysOffEvenIfItWouldHaveBecomeHealthy() async {
        let l = FakeLFMLauncher(); l.setHealthy(false); let h = host(l)
        h.enable(classMode: false)
        h.disable()
        l.setHealthy(true)
        try? await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(h.state, .off)
        XCTAssertEqual(l.last?.terminated, 1)
    }

    func testShutdownStopsAndKillsRightAway() async {
        let l = FakeLFMLauncher(); l.nextIgnoresTerminate = true; let h = host(l, grace: .seconds(30))
        h.enable(classMode: false)
        await until { h.state == .ready }
        h.shutdown()
        XCTAssertEqual(l.last?.terminated, 1)
        XCTAssertEqual(l.last?.killed, 1, "on quit there is no time to wait")
        XCTAssertEqual(h.state, .off)
    }

    func testItCanBeTurnedOnAgainAfterAFailureAndAfterOff() async {
        let l = FakeLFMLauncher(); l.launchError = Boom(); let h = host(l)
        h.enable(classMode: false)
        l.launchError = nil
        h.enable(classMode: false)
        await until { h.state == .ready }
        h.disable()
        h.enable(classMode: false)
        await until { h.state == .ready }
        XCTAssertEqual(l.launches, 2)
        XCTAssertEqual(h.port, 51003, "the failed try used 51001; each start picks a fresh port")
    }

    func testAClassOpeningTurnsItOff() async {
        let l = FakeLFMLauncher(); let h = host(l)
        h.enable(classMode: false)
        await until { h.state == .ready }
        h.classBegan()
        XCTAssertEqual(h.state, .off)
        XCTAssertEqual(l.last?.terminated, 1)
    }

    func testDisableWhenOffIsHarmless() {
        let l = FakeLFMLauncher(); let h = host(l)
        h.disable()
        XCTAssertEqual(h.state, .off)
    }

    func testReapingOrphansIsPassedToTheLauncher() {
        let l = FakeLFMLauncher(); let h = host(l)
        h.reapOrphans()
        XCTAssertEqual(l.reaps, 1)
    }

    // The voice object wires class mode to the toggle: a class opening frees the model's memory.
    func testVoiceClassModeTurnsTheToggleOff() async {
        let l = FakeLFMLauncher(); let h = host(l)
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api), lfm: h)
        h.enable(classMode: false)
        await until { h.state == .ready }
        v.classMode = true
        XCTAssertEqual(h.state, .off)
        XCTAssertEqual(l.last?.terminated, 1)
    }
}

final class LFMWireTests: XCTestCase {
    func testPcm16ConvertsAndClamps() {
        let d = LFMWire.pcm16([0, 1, -1, 2, -2, 0.5])
        XCTAssertEqual(d.count, 12)
        let v = d.withUnsafeBytes { Array($0.bindMemory(to: Int16.self)) }.map { Int16(littleEndian: $0) }
        XCTAssertEqual(v[0], 0)
        XCTAssertEqual(v[1], 32767)
        XCTAssertEqual(v[2], -32767)
        XCTAssertEqual(v[3], 32767, "over-range is clamped, not wrapped")
        XCTAssertEqual(v[4], -32767)
        XCTAssertEqual(Int(v[5]), 16383, accuracy: 1)
    }

    func testFloatsFromPcm16RoundTripsAt24k() {
        let d = LFMWire.pcm16([0, 0.25, -0.25])
        let f = LFMWire.floats(fromPCM16: d)
        XCTAssertEqual(f.count, 3)
        XCTAssertEqual(f[1], 0.25, accuracy: 0.001)
        XCTAssertEqual(f[2], -0.25, accuracy: 0.001)
    }

    func testAppendEventsSplitTheWaveAndCarryBase64() throws {
        let wave = [Float](repeating: 0.1, count: 16000 * 3 + 5)       // a little over 3 s at 16 kHz
        let events = LFMWire.appendEvents(wave, secondsPerEvent: 1)
        XCTAssertEqual(events.count, 4)
        var total = 0
        for e in events {
            let o = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(e.utf8)) as? [String: Any])
            XCTAssertEqual(o["type"] as? String, "input_audio_buffer.append")
            total += (try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(o["audio"] as? String)))).count
        }
        XCTAssertEqual(total, (16000 * 3 + 5) * 2)
    }

    func testSessionUpdateAsksForAudioBack() throws {
        let o = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(LFMWire.sessionUpdate.utf8)) as? [String: Any])
        XCTAssertEqual(o["type"] as? String, "session.update")
        let s = try XCTUnwrap(o["session"] as? [String: Any])
        XCTAssertEqual(s["output_modalities"] as? [String], ["audio"])
    }

    func testCommitAndCreateEvents() {
        XCTAssertTrue(LFMWire.commit.contains("input_audio_buffer.commit"))
        XCTAssertTrue(LFMWire.create.contains("response.create"))
    }

    func testParsingTheServersEvents() {
        let pcm = Data([1, 0, 2, 0])
        let audio = #"{"type":"response.output_audio.delta","delta":"\#(pcm.base64EncodedString())"}"#
        XCTAssertEqual(LFMWire.parse(audio), .audio(pcm))
        XCTAssertEqual(LFMWire.parse(#"{"type":"response.done","response":{}}"#), .done)
        XCTAssertEqual(LFMWire.parse(#"{"type":"response.output_text.delta","delta":"x"}"#), .other)
        XCTAssertEqual(LFMWire.parse(#"{"type":"error","error":{"message":"bad"}}"#), .error("bad"))
        XCTAssertEqual(LFMWire.parse("not json"), .other)
        XCTAssertEqual(LFMWire.parse(#"{"type":"response.output_audio.delta"}"#), .other, "no payload: ignore")
    }

    func testTheLogLineHasTheLatencyAndLength() {
        XCTAssertEqual(LFMWire.logLine(firstAudioMs: 612, audioSeconds: 2.24, listenSeconds: 3.1),
                       "LFM first_audio_ms=612 audio_s=2.24 heard_s=3.1")
        XCTAssertEqual(LFMWire.logLine(firstAudioMs: nil, audioSeconds: 0, listenSeconds: 1),
                       "LFM first_audio_ms=none audio_s=0.00 heard_s=1.0")
    }

    func testTheLaunchArgsMatchTheCard() {
        XCTAssertEqual(RealLFMLauncher.args(port: 8123),
                       ["serve", "-hf", "LiquidAI/LFM3-Omni-2B-GGUF:Q4_K_M", "--reasoning", "off", "--temp", "0",
                        "--host", "127.0.0.1", "--port", "8123"])
    }
}
