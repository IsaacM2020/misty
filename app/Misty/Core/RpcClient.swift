import Foundation
#if canImport(Darwin)
import Darwin
#endif

public enum RpcError: Error, Equatable, Sendable {
    case disconnected
    case timeout
    case server(code: String)
    case decoding(String)
}

public protocol RpcTransport: Sendable {
    /// One element per received line (without the newline).
    var lines: AsyncThrowingStream<Data, Error> { get }
    func send(_ data: Data) async throws
    func close() async
}

/// What view models need from the engine connection (so tests can fake it).
public protocol EngineAPI: Sendable {
    func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P) async throws -> R
    /// For the few calls that legitimately run long (a Go that drives Stream).
    func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P, timeout: Duration) async throws -> R
}

public extension EngineAPI {
    func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P, timeout: Duration) async throws -> R {
        try await call(method, params)
    }
}

public actor RpcClient: EngineAPI {
    private let transport: RpcTransport
    private var nextId = 1
    private var pending: [Int: CheckedContinuation<JSONValue, Error>] = [:]
    private var reader: Task<Void, Never>?
    private let eventContinuation: AsyncStream<RpcEvent>.Continuation
    public nonisolated let events: AsyncStream<RpcEvent>

    public init(socketPath: URL, transport: RpcTransport? = nil) {
        self.transport = transport ?? UnixSocketTransport(path: socketPath.path)
        var cont: AsyncStream<RpcEvent>.Continuation!
        self.events = AsyncStream { cont = $0 }
        self.eventContinuation = cont
    }

    public func connect() async throws {
        if reader != nil { return }
        let lines = transport.lines
        reader = Task { [weak self] in
            do {
                for try await line in lines { await self?.handle(line) }
            } catch {}
            await self?.failAll(RpcError.disconnected)
        }
    }

    public func disconnect() {
        reader?.cancel()
        reader = nil
        Task { await transport.close() }
        failAll(RpcError.disconnected)
        eventContinuation.finish()
    }

    private func failAll(_ error: Error) {
        let all = pending
        pending = [:]
        for (_, c) in all { c.resume(throwing: error) }
    }

    private func handle(_ line: Data) {
        guard let obj = try? Wire.plainDecoder().decode(Envelope.self, from: line) else { return }
        if let id = obj.id, let cont = pending.removeValue(forKey: id) {
            if let err = obj.error {
                cont.resume(throwing: RpcError.server(code: err.message))
            } else {
                cont.resume(returning: obj.result ?? .null)
            }
            return
        }
        if obj.method != nil, let ev = try? RpcEvent.decode(line) {
            eventContinuation.yield(ev)
        }
    }

    private struct Envelope: Decodable {
        struct Err: Decodable { var code: Int; var message: String }
        var id: Int?
        var method: String?
        var result: JSONValue?
        var error: Err?
    }

    private struct Request<P: Encodable>: Encodable {
        var jsonrpc = "2.0"
        var id: Int
        var method: String
        var params: P
    }

    public func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P) async throws -> R {
        try await call(method, params, timeout: .seconds(30))
    }

    public func call<P: Encodable & Sendable, R: Decodable & Sendable>(
        _ method: String, _ params: P, timeout: Duration
    ) async throws -> R {
        let id = nextId
        nextId += 1
        let payload = try Wire.encoder().encode(Request(id: id, method: method, params: params)) + Data([0x0A])
        let value: JSONValue = try await withCheckedThrowingContinuation { (cont: CheckedContinuation<JSONValue, Error>) in
            begin(id: id, cont: cont, payload: payload, timeout: timeout)
        }
        let data = try Wire.plainEncoder().encode(value)
        do { return try Wire.decoder().decode(R.self, from: data) }
        catch { throw RpcError.decoding(String(describing: error)) }
    }

    private func begin(id: Int, cont: CheckedContinuation<JSONValue, Error>, payload: Data, timeout: Duration) {
        pending[id] = cont
        Task { await self.transmit(id: id, payload: payload) }
        Task {
            try? await Task.sleep(for: timeout)
            await self.expire(id)
        }
    }

    private func transmit(id: Int, payload: Data) async {
        do { try await transport.send(payload) }
        catch { if let c = pending.removeValue(forKey: id) { c.resume(throwing: RpcError.disconnected) } }
    }

    private func expire(_ id: Int) {
        if let c = pending.removeValue(forKey: id) { c.resume(throwing: RpcError.timeout) }
    }
}

// MARK: - real transport

public final class UnixSocketTransport: RpcTransport, @unchecked Sendable {
    private let path: String
    private var fd: Int32 = -1
    private let writeQueue = DispatchQueue(label: "misty.rpc.write")
    private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
    public let lines: AsyncThrowingStream<Data, Error>

    public init(path: String) {
        self.path = path
        var cont: AsyncThrowingStream<Data, Error>.Continuation!
        self.lines = AsyncThrowingStream { cont = $0 }
        self.continuation = cont
        start()
    }

    private func start() {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))      // a closed peer is an error to handle, never a signal that kills the app
        guard fd >= 0 else { continuation?.finish(throwing: RpcError.disconnected); return }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8CString)
        withUnsafeMutableBytes(of: &addr.sun_path) { raw in
            for (i, b) in bytes.prefix(raw.count - 1).enumerated() { raw[i] = UInt8(bitPattern: b) }
        }
        let len = socklen_t(MemoryLayout<sockaddr_un>.size)
        let ok = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, len) }
        }
        guard ok == 0 else {
            Darwin.close(fd)
            continuation?.finish(throwing: RpcError.disconnected)
            return
        }
        self.fd = fd
        let cont = continuation
        DispatchQueue.global(qos: .utility).async {
            var buffer = Data()
            var chunk = [UInt8](repeating: 0, count: 65536)
            while true {
                let n = read(fd, &chunk, chunk.count)
                if n <= 0 { break }
                buffer.append(chunk, count: n)
                while let nl = buffer.firstIndex(of: 0x0A) {
                    let line = buffer.subdata(in: buffer.startIndex..<nl)
                    buffer.removeSubrange(buffer.startIndex...nl)
                    if !line.isEmpty { cont?.yield(line) }
                }
            }
            cont?.finish()
        }
    }

    public func send(_ data: Data) async throws {
        let fd = self.fd
        guard fd >= 0 else { throw RpcError.disconnected }
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            writeQueue.async {
                var off = 0
                let ok: Bool = data.withUnsafeBytes { raw in
                    while off < raw.count {
                        let n = write(fd, raw.baseAddress!.advanced(by: off), raw.count - off)
                        if n <= 0 { return false }
                        off += n
                    }
                    return true
                }
                if ok { c.resume() } else { c.resume(throwing: RpcError.disconnected) }
            }
        }
    }

    public func close() async {
        if fd >= 0 { Darwin.close(fd); fd = -1 }
        continuation?.finish()
    }
}
