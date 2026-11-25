import Foundation
import XCTest
import CryptoKit
@testable import MistyCore

final class FakeTransport: RpcTransport, @unchecked Sendable {
    let lines: AsyncThrowingStream<Data, Error>
    private let cont: AsyncThrowingStream<Data, Error>.Continuation
    private let lock = NSLock()
    private var _sent: [Data] = []
    var sent: [Data] { lock.lock(); defer { lock.unlock() }; return _sent }

    init() {
        var c: AsyncThrowingStream<Data, Error>.Continuation!
        lines = AsyncThrowingStream { c = $0 }
        cont = c
    }

    private func append(_ d: Data) { lock.lock(); _sent.append(d); lock.unlock() }
    func send(_ data: Data) async throws { append(data) }
    func close() async { cont.finish() }
    func feed(_ data: Data) { cont.yield(data) }
    func feedString(_ s: String) { cont.yield(Data(s.utf8)) }
    func drop() { cont.finish(throwing: RpcError.disconnected) }

    func reply(to id: Int, resultJSON: String) { feedString(#"{"jsonrpc":"2.0","id":\#(id),"result":\#(resultJSON)}"#) }
    func replyError(to id: Int, message: String) {
        feedString(#"{"jsonrpc":"2.0","id":\#(id),"error":{"code":-32000,"message":"\#(message)"}}"#)
    }

    func waitForSent(_ n: Int, timeout: Double = 3) async {
        let end = Date().addingTimeInterval(timeout)
        while sent.count < n && Date() < end { try? await Task.sleep(for: .milliseconds(5)) }
    }
}

func contractDir() -> URL {
    if let env = ProcessInfo.processInfo.environment["MISTY_CONTRACT_DIR"] { return URL(fileURLWithPath: env) }
    return URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("tests/contract")
}

func fixtureText(_ name: String) throws -> String {
    try String(contentsOf: contractDir().appendingPathComponent("\(name).json"), encoding: .utf8)
}

func fixtureData(_ name: String) throws -> Data { Data(try fixtureText(name).utf8) }

/// A fake engine connection for view-model tests.
final class FakeAPI: EngineAPI, @unchecked Sendable {
    private let lock = NSLock()
    var responses: [String: String] = [:]          // method -> JSON result
    var failing: Set<String> = []
    private(set) var calls: [(method: String, params: String)] = []

    private func record(_ m: String, _ p: String) { lock.lock(); calls.append((m, p)); lock.unlock() }

    func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P) async throws -> R {
        let p = String(data: try Wire.encoder().encode(params), encoding: .utf8) ?? ""
        record(method, p)
        if failing.contains(method) { throw RpcError.server(code: "BOOM") }
        let json = responses[method] ?? "{}"
        return try Wire.decoder().decode(R.self, from: Data(json.utf8))
    }
}

func sha256Hex(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }
