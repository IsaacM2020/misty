import Foundation

public protocol RunningEngine: Sendable {
    /// Yields the exit status once, when the process ends.
    var terminated: AsyncStream<Int32> { get }
    func terminate()
}

public protocol ProcessLauncher: Sendable {
    func launch() throws -> RunningEngine
}

public protocol SupervisorClock: Sendable {
    func now() -> Double
    func sleep(seconds: Double) async throws
}

public struct SystemSupervisorClock: SupervisorClock {
    public init() {}
    public func now() -> Double { Date().timeIntervalSince1970 }
    public func sleep(seconds: Double) async throws { try await Task.sleep(for: .seconds(seconds)) }
}

public enum EngineState: Equatable, Sendable {
    case stopped
    case starting
    case running
    case backoff(seconds: Int)
    case crashLoop
}

/// Keeps the engine alive: restart with 2, 4, 8, 16, 30, 30 ... s delays, delay resets after 60 s of stable running,
/// `.crashLoop` after 3 crashes within 5 minutes (restarts continue at the capped delay).
public actor EngineSupervisor {
    public static let delays = [2, 4, 8, 16, 30]
    public static let stableAfter = 60.0
    public static let crashWindow = 300.0
    public static let crashLimit = 3

    private let launcher: ProcessLauncher
    private let clock: SupervisorClock
    private let onState: @Sendable (EngineState) -> Void
    private var task: Task<Void, Never>?
    private var current: RunningEngine?
    private var stopped = false
    private(set) var crashTimes: [Double] = []
    public private(set) var state: EngineState = .stopped

    public init(launcher: ProcessLauncher, clock: SupervisorClock = SystemSupervisorClock(),
                onState: @escaping @Sendable (EngineState) -> Void = { _ in }) {
        self.launcher = launcher
        self.clock = clock
        self.onState = onState
    }

    private func set(_ s: EngineState) {
        state = s
        onState(s)
    }

    public func start() {
        guard task == nil else { return }
        stopped = false
        task = Task { await self.loop() }
    }

    public func stop() {
        stopped = true
        current?.terminate()
        task?.cancel()
        task = nil
        set(.stopped)
    }

    private func loop() async {
        var delayIndex = 0
        while !stopped && !Task.isCancelled {
            set(.starting)
            let engine: RunningEngine
            do { engine = try launcher.launch() }
            catch {
                crashTimes.append(clock.now())
                if await !wait(&delayIndex) { return }
                continue
            }
            current = engine
            set(.running)
            let startedAt = clock.now()
            for await _ in engine.terminated { break }
            current = nil
            if stopped || Task.isCancelled { return }
            let now = clock.now()
            if now - startedAt >= Self.stableAfter { delayIndex = 0 }
            crashTimes.append(now)
            if await !wait(&delayIndex) { return }
        }
    }

    /// Records the crash, publishes the state and sleeps the backoff. False when the supervisor was stopped.
    private func wait(_ delayIndex: inout Int) async -> Bool {
        let now = clock.now()
        crashTimes = crashTimes.filter { now - $0 <= Self.crashWindow }
        let delay = Self.delays[min(delayIndex, Self.delays.count - 1)]
        delayIndex += 1
        set(crashTimes.count >= Self.crashLimit ? .crashLoop : .backoff(seconds: delay))
        do { try await clock.sleep(seconds: Double(delay)) } catch { return false }
        return !stopped
    }
}
