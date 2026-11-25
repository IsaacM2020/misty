import Foundation
import Observation
import AppKit

/// V4: an experimental toggle. On, EDITH runs Liquid's LFM3-Omni-2B (speech in, speech out) as a child `llama serve` process on this
/// Mac and push-to-talk goes to it. Off, the child is killed and its memory is given back. Never on at launch, never in class mode.

public enum LFMState: Equatable, Sendable {
    case off, starting, ready, failed(String)
    public var isFailure: Bool { if case .failed = self { return true } else { return false } }
}

/// The running child. The real one wraps a Foundation.Process; tests use a fake.
public protocol LFMChild: AnyObject, Sendable {
    var isRunning: Bool { get }
    func terminate()                                  // SIGTERM: ask nicely
    func kill()                                       // SIGKILL
    func onExit(_ f: @escaping @Sendable () -> Void)  // called once when the child is gone, for any reason
}

public protocol LFMLauncher: Sendable {
    func freePort() -> Int
    func launch(port: Int) throws -> LFMChild
    func isHealthy(port: Int) async -> Bool
    /// A child left behind by an app that crashed (its pid is in a file): stop it.
    func reapOrphans()
}

@MainActor @Observable
public final class LFMHost {
    public private(set) var state: LFMState = .off
    public private(set) var port: Int?
    @ObservationIgnored private let launcher: LFMLauncher
    @ObservationIgnored private let readyTimeout: Duration
    @ObservationIgnored private let poll: Duration
    @ObservationIgnored private let graceKill: Duration
    @ObservationIgnored private var child: LFMChild?
    @ObservationIgnored private var generation = 0          // which start this is, so a late callback from an old child is ignored
    @ObservationIgnored private var pollTask: Task<Void, Never>?
    @ObservationIgnored private var quitWatch: NSObjectProtocol?

    /// `readyTimeout` is long on purpose: the first run downloads about 1.4 GB.
    public init(launcher: LFMLauncher, readyTimeout: Duration = .seconds(900), poll: Duration = .milliseconds(300),
                graceKill: Duration = .seconds(3), observeQuit: Bool = true) {
        self.launcher = launcher
        self.readyTimeout = readyTimeout
        self.poll = poll
        self.graceKill = graceKill
        if observeQuit {
            quitWatch = NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.shutdown() }
            }
        }
    }

    /// Push-to-talk goes to the model only when it is up, and never in class mode.
    public func canRoute(classMode: Bool) -> Bool { state == .ready && !classMode }

    public func reapOrphans() { launcher.reapOrphans() }

    public func enable(classMode: Bool) {
        guard !classMode else { return }
        switch state {
        case .starting, .ready: return
        case .off, .failed: break
        }
        generation += 1
        let gen = generation
        let p = launcher.freePort()
        do {
            let c = try launcher.launch(port: p)
            child = c
            port = p
            state = .starting
            VoiceLog.write("LFM start port=\(p)")
            c.onExit { [weak self] in Task { @MainActor in self?.childExited(gen) } }
            pollTask = Task { [weak self] in await self?.waitReady(p, gen) }
        } catch {
            state = .failed(error.localizedDescription)
            VoiceLog.write("LFM start_failed \(error.localizedDescription)")
        }
    }

    /// Turn it off: the child is asked to stop, then killed if it has not gone after a short grace. Returns at once.
    public func disable() {
        generation += 1
        pollTask?.cancel(); pollTask = nil
        let was = state
        state = .off
        port = nil
        guard let c = child else { return }
        child = nil
        c.terminate()
        let grace = graceKill
        if c.isRunning {
            Task.detached { try? await Task.sleep(for: grace); if c.isRunning { c.kill() } }
        }
        if was != .off { VoiceLog.write("LFM stop") }
    }

    /// A class opened: it must not be running.
    public func classBegan() { if state != .off { disable() } }

    /// The app is quitting: stop and kill now, no waiting.
    public func shutdown() {
        generation += 1
        pollTask?.cancel(); pollTask = nil
        state = .off
        port = nil
        guard let c = child else { return }
        child = nil
        c.terminate()
        if c.isRunning { c.kill() }
        VoiceLog.write("LFM stop (quit)")
    }

    private func waitReady(_ p: Int, _ gen: Int) async {
        let deadline = ContinuousClock.now + readyTimeout
        while !Task.isCancelled, gen == generation, ContinuousClock.now < deadline {
            if await launcher.isHealthy(port: p) {
                guard gen == generation, state == .starting else { return }
                state = .ready
                VoiceLog.write("LFM ready port=\(p)")
                return
            }
            try? await Task.sleep(for: poll)
        }
        guard gen == generation, state == .starting else { return }
        fail("LFM3 voice didn't start in time", stop: true)
    }

    private func childExited(_ gen: Int) {
        guard gen == generation, state == .starting || state == .ready else { return }
        fail("LFM3 voice stopped", stop: false)
    }

    private func fail(_ why: String, stop: Bool) {
        generation += 1
        pollTask?.cancel(); pollTask = nil
        port = nil
        state = .failed(why)
        VoiceLog.write("LFM failed \(why)")
        if let c = child {
            child = nil
            if stop { c.terminate(); if c.isRunning { let g = graceKill; Task.detached { try? await Task.sleep(for: g); if c.isRunning { c.kill() } } } }
        }
    }
}

// MARK: the real child

public final class RealLFMChild: LFMChild, @unchecked Sendable {
    private let process: Process
    private let lock = NSLock()
    private var handler: (@Sendable () -> Void)?
    private var exited = false
    init(process: Process) throws {
        self.process = process
        process.terminationHandler = { [weak self] _ in self?.fire() }
        try process.run()
    }
    private func fire() {
        let h: (@Sendable () -> Void)? = lock.withLock { exited = true; return handler }
        h?()
    }
    public var isRunning: Bool { process.isRunning }
    public func terminate() { if process.isRunning { process.terminate() } }
    public func kill() { if process.isRunning { Foundation.kill(process.processIdentifier, SIGKILL) } }
    public func onExit(_ f: @escaping @Sendable () -> Void) {
        let already: Bool = lock.withLock { handler = f; return exited }
        if already { f() }
    }
    var pid: Int32 { process.processIdentifier }
}

public final class RealLFMLauncher: LFMLauncher, @unchecked Sendable {
    public static let model = "LiquidAI/LFM3-Omni-2B-GGUF:Q4_K_M"
    static let pidURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Misty/lfm.pid")
    static let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Misty/logs/lfm.log")

    public init() {}

    public static func args(port: Int) -> [String] {
        ["serve", "-hf", model, "--reasoning", "off", "--temp", "0", "--host", "127.0.0.1", "--port", String(port)]
    }

    public static func binary() -> String? {
        ["/opt/homebrew/bin/llama", "/usr/local/bin/llama"].first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    public func freePort() -> Int {
        let s = socket(AF_INET, SOCK_STREAM, 0)
        guard s >= 0 else { return 8791 }
        defer { close(s) }
        var a = sockaddr_in()
        a.sin_family = sa_family_t(AF_INET)
        a.sin_addr.s_addr = inet_addr("127.0.0.1")
        a.sin_port = 0
        let bound = withUnsafePointer(to: &a) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(s, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
        guard bound == 0 else { return 8791 }
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let got = withUnsafeMutablePointer(to: &a) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(s, $0, &len) } }
        return got == 0 ? Int(UInt16(bigEndian: a.sin_port)) : 8791
    }

    public func launch(port: Int) throws -> LFMChild {
        guard let bin = Self.binary() else {
            throw NSError(domain: "LFM", code: 1, userInfo: [NSLocalizedDescriptionKey: "llama.cpp isn't installed (brew install llama.cpp)"])
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: bin)
        p.arguments = Self.args(port: port)
        try? FileManager.default.createDirectory(at: Self.logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: Self.logURL.path) { FileManager.default.createFile(atPath: Self.logURL.path, contents: nil) }
        if let h = try? FileHandle(forWritingTo: Self.logURL) { h.seekToEndOfFile(); p.standardOutput = h; p.standardError = h }
        p.standardInput = FileHandle.nullDevice
        let c = try RealLFMChild(process: p)
        try? String(c.pid).write(to: Self.pidURL, atomically: true, encoding: .utf8)
        return c
    }

    public func isHealthy(port: Int) async -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/health") else { return false }
        var r = URLRequest(url: url); r.timeoutInterval = 2
        guard let (_, resp) = try? await URLSession.shared.data(for: r) else { return false }
        return (resp as? HTTPURLResponse)?.statusCode == 200
    }

    public func reapOrphans() {
        guard let s = try? String(contentsOf: Self.pidURL, encoding: .utf8), let pid = Int32(s.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 1 else { return }
        defer { try? FileManager.default.removeItem(at: Self.pidURL) }
        guard kill(pid, 0) == 0 else { return }
        let ps = Process()
        ps.executableURL = URL(fileURLWithPath: "/bin/ps")
        ps.arguments = ["-p", String(pid), "-o", "command="]
        let pipe = Pipe(); ps.standardOutput = pipe
        guard (try? ps.run()) != nil else { return }
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        ps.waitUntilExit()
        if out.contains("LFM3-Omni") { kill(pid, SIGTERM); VoiceLog.write("LFM reaped orphan pid=\(pid)") }
    }
}
