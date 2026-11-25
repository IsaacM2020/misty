import XCTest
@testable import MistyCore

final class VirtualClock: SupervisorClock, @unchecked Sendable {
    private let lock = NSLock()
    private var t = 1_000.0
    private var _sleeps: [Double] = []
    var sleeps: [Double] { lock.lock(); defer { lock.unlock() }; return _sleeps }
    func now() -> Double { lock.lock(); defer { lock.unlock() }; return t }
    func advance(_ s: Double) { lock.lock(); t += s; lock.unlock() }
    private func record(_ seconds: Double) { lock.lock(); _sleeps.append(seconds); t += seconds; lock.unlock() }
    func sleep(seconds: Double) async throws {
        record(seconds)
        await Task.yield()
    }
}

final class FakeEngine: RunningEngine, @unchecked Sendable {
    let terminated: AsyncStream<Int32>
    private let cont: AsyncStream<Int32>.Continuation
    private(set) var terminateCalls = 0
    init() {
        var c: AsyncStream<Int32>.Continuation!
        terminated = AsyncStream { c = $0 }
        cont = c
    }
    func crash() { cont.yield(1); cont.finish() }
    func terminate() { terminateCalls += 1; cont.finish() }
}

final class FakeLauncher: ProcessLauncher, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var engines: [FakeEngine] = []
    func launch() throws -> RunningEngine {
        let e = FakeEngine()
        lock.lock(); engines.append(e); lock.unlock()
        return e
    }
    var count: Int { lock.lock(); defer { lock.unlock() }; return engines.count }
    func waitForLaunches(_ n: Int) async {
        let end = Date().addingTimeInterval(3)
        while count < n && Date() < end { try? await Task.sleep(for: .milliseconds(2)) }
    }
}

final class StateLog: @unchecked Sendable {
    private let lock = NSLock()
    private var _s: [EngineState] = []
    var states: [EngineState] { lock.lock(); defer { lock.unlock() }; return _s }
    func add(_ s: EngineState) { lock.lock(); _s.append(s); lock.unlock() }
}

final class SupervisorTests: XCTestCase {
    func crashAndWait(_ l: FakeLauncher, index: Int) async {
        await l.waitForLaunches(index + 1)
        l.engines[index].crash()
    }

    func testBackoffSequence() async {
        let l = FakeLauncher(), clock = VirtualClock(), log = StateLog()
        let sup = EngineSupervisor(launcher: l, clock: clock) { log.add($0) }
        await sup.start()
        for i in 0..<6 { await crashAndWait(l, index: i) }
        await l.waitForLaunches(7)
        await sup.stop()
        XCTAssertEqual(clock.sleeps.prefix(6), [2, 4, 8, 16, 30, 30])
    }

    func testBackoffResetsAfterStableRun() async {
        let l = FakeLauncher(), clock = VirtualClock()
        let sup = EngineSupervisor(launcher: l, clock: clock)
        await sup.start()
        await crashAndWait(l, index: 0)             // delay 2
        await l.waitForLaunches(2)
        clock.advance(61)                           // engine 2 runs stably for 61 s
        l.engines[1].crash()
        await l.waitForLaunches(3)
        await sup.stop()
        XCTAssertEqual(Array(clock.sleeps.prefix(2)), [2, 2])
    }

    func testCrashLoopWarningAfterThreeCrashesInFiveMinutes() async {
        let l = FakeLauncher(), clock = VirtualClock(), log = StateLog()
        let sup = EngineSupervisor(launcher: l, clock: clock) { log.add($0) }
        await sup.start()
        for i in 0..<3 { await crashAndWait(l, index: i) }
        await l.waitForLaunches(4)
        await sup.stop()
        XCTAssertTrue(log.states.contains(.crashLoop))
    }

    func testTwoCrashesSpreadOverTenMinutesNoWarning() async {
        let l = FakeLauncher(), clock = VirtualClock(), log = StateLog()
        let sup = EngineSupervisor(launcher: l, clock: clock) { log.add($0) }
        await sup.start()
        await l.waitForLaunches(1)
        clock.advance(400); l.engines[0].crash()
        await l.waitForLaunches(2)
        clock.advance(400); l.engines[1].crash()
        await l.waitForLaunches(3)
        clock.advance(400); l.engines[2].crash()
        await l.waitForLaunches(4)
        await sup.stop()
        XCTAssertFalse(log.states.contains(.crashLoop))
    }

    func testStopTerminatesChildAndDoesNotRestart() async {
        let l = FakeLauncher(), clock = VirtualClock()
        let sup = EngineSupervisor(launcher: l, clock: clock)
        await sup.start()
        await l.waitForLaunches(1)
        await sup.stop()
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(l.engines[0].terminateCalls, 1)
        XCTAssertEqual(l.count, 1)
    }
}
