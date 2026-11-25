import Foundation

final class ProcessEngine: RunningEngine, @unchecked Sendable {
    let terminated: AsyncStream<Int32>
    private let process: Process

    init(process: Process) throws {
        var cont: AsyncStream<Int32>.Continuation!
        terminated = AsyncStream { cont = $0 }
        self.process = process
        let c = cont!
        process.terminationHandler = { p in
            c.yield(p.terminationStatus)
            c.finish()
        }
        try process.run()
    }

    func terminate() {
        if process.isRunning { process.terminate() }
    }
}

/// Launches the bundled Python engine as a child of the app (so it inherits the app's Full Disk Access).
public struct RealLauncher: ProcessLauncher {
    public let socketPath: String
    public let mistyHome: String
    public let parentPid: Int32
    public let helpersDir: URL?

    public init(socketPath: String, mistyHome: String, helpersDir: URL?,
                parentPid: Int32 = ProcessInfo.processInfo.processIdentifier) {
        self.socketPath = socketPath
        self.mistyHome = mistyHome
        self.helpersDir = helpersDir
        self.parentPid = parentPid
    }

    /// The engine's environment. Bytecode is precompiled into the bundle before signing (bundle_engine.sh) and
    /// never written at run time: a .pyc written inside Misty.app would break the app's signature (and with it
    /// the Full Disk Access grant), and a pycache prefix mirrors paths like ".../Misty.app" that launchers list as apps.
    public static func environment(mistyHome: String) -> [String: String] {
        ["PATH": "/usr/bin:/bin", "MISTY_HOME": mistyHome, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
         "HOME": NSHomeDirectory(), "USER": NSUserName(), "LOGNAME": NSUserName(), "LANG": "en_US.UTF-8"]
    }

    public func launch() throws -> RunningEngine {
        let p = Process()
        var env = Self.environment(mistyHome: mistyHome)
        if let cmd = ProcessInfo.processInfo.environment["MISTY_ENGINE_CMD"] {
            // Debug / development: "uv run --project /path/engine python -m misty_engine" or similar, run via the shell
            p.executableURL = URL(fileURLWithPath: "/bin/sh")
            p.arguments = ["-c", "\(cmd) serve --socket '\(socketPath)' --parent-pid \(parentPid)"]
            env["PATH"] = ProcessInfo.processInfo.environment["PATH"] ?? env["PATH"]
        } else if let dir = helpersDir {
            p.executableURL = dir.appendingPathComponent("python/bin/python3.12")
            env["MISTY_CONFIG"] = dir.appendingPathComponent("config/pinned.toml").path   // shipped by bundle_engine.sh
            p.arguments = ["-m", "misty_engine", "serve", "--socket", socketPath, "--parent-pid", String(parentPid)]
        } else {
            throw RpcError.disconnected
        }
        p.environment = env
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        return try ProcessEngine(process: p)
    }
}

/// Wraps whichever RpcClient is current; view models hold this so they survive engine restarts.
public final class LiveAPI: EngineAPI, @unchecked Sendable {
    private let lock = NSLock()
    private var client: RpcClient?

    public init() {}

    public func set(_ c: RpcClient?) { lock.lock(); client = c; lock.unlock() }
    private func current() -> RpcClient? { lock.lock(); defer { lock.unlock() }; return client }

    public func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P) async throws -> R {
        guard let c = current() else { throw RpcError.disconnected }
        return try await c.call(method, params)
    }

    public func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P, timeout: Duration) async throws -> R {
        guard let c = current() else { throw RpcError.disconnected }
        return try await c.call(method, params, timeout: timeout)
    }
}
