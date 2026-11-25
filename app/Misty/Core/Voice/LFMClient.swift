import AVFoundation
import Foundation

/// V4: the wire format of the local server's `/v1/realtime` WebSocket (OpenAI-realtime style, checked against llama.cpp 1.0.0 on
/// 17 Nov): it takes 16 kHz mono PCM16 as base64 chunks, `commit` + `response.create` ask for the reply, and with
/// `output_modalities: ["audio"]` the reply comes back as 24 kHz mono PCM16 `response.output_audio.delta` events.
public enum LFMWire {
    public static let sessionUpdate = #"{"type":"session.update","session":{"type":"realtime","output_modalities":["audio"]}}"#
    public static let commit = #"{"type":"input_audio_buffer.commit"}"#
    public static let create = #"{"type":"response.create"}"#
    public static let inputRate = 16000
    public static let outputRate = 24000

    public enum Event: Equatable, Sendable { case audio(Data), done, error(String), sessionUpdated, other }

    public static func pcm16(_ f: [Float]) -> Data {
        var out = Data(capacity: f.count * 2)
        for x in f {
            let v = Int16(max(-1, min(1, x)) * 32767)
            withUnsafeBytes(of: v.littleEndian) { out.append(contentsOf: $0) }
        }
        return out
    }

    public static func floats(fromPCM16 d: Data) -> [Float] {
        let n = d.count / 2
        return d.withUnsafeBytes { raw in
            (0..<n).map { i in Float(Int16(littleEndian: raw.loadUnaligned(fromByteOffset: i * 2, as: Int16.self))) / 32767 }
        }
    }

    public static func appendEvents(_ wave: [Float], secondsPerEvent: Double = 1) -> [String] {
        let step = max(1, Int(Double(inputRate) * secondsPerEvent))
        return stride(from: 0, to: wave.count, by: step).map { i in
            let b64 = pcm16(Array(wave[i..<min(i + step, wave.count)])).base64EncodedString()
            return #"{"type":"input_audio_buffer.append","audio":"\#(b64)"}"#
        }
    }

    public static func parse(_ text: String) -> Event {
        guard let o = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any], let t = o["type"] as? String else { return .other }
        switch t {
        case "response.output_audio.delta":
            guard let s = o["delta"] as? String, let d = Data(base64Encoded: s) else { return .other }
            return .audio(d)
        case "response.done": return .done
        case "session.updated": return .sessionUpdated
        case "error": return .error(((o["error"] as? [String: Any])?["message"] as? String) ?? "error")
        default: return .other
        }
    }

    public static func logLine(firstAudioMs: Int?, audioSeconds: Double, listenSeconds: Double) -> String {
        "LFM first_audio_ms=\(firstAudioMs.map(String.init) ?? "none") " + String(format: "audio_s=%.2f heard_s=%.1f", audioSeconds, listenSeconds)
    }
}

public struct LFMTurnResult: Sendable { public var firstAudioMs: Int?; public var audioSeconds: Double }

public enum LFMClient {
    /// One spoken turn: a fresh connection (the model keeps no memory between turns here), the whole recording in, the reply out.
    /// `onAudio` runs on the main actor with each 24 kHz PCM16 piece. Throws on a server error or after `timeout` seconds.
    public static func run(port: Int, wave: [Float], timeout: Double = 60,
                           onAudio: @MainActor @Sendable (Data) -> Void) async throws -> LFMTurnResult {
        guard let url = URL(string: "ws://127.0.0.1:\(port)/v1/realtime") else { throw URLError(.badURL) }
        let task = URLSession.shared.webSocketTask(with: url)
        task.maximumMessageSize = 64 << 20
        task.resume()
        let watchdog = Task { try? await Task.sleep(for: .seconds(timeout)); task.cancel(with: .goingAway, reason: nil) }
        defer { watchdog.cancel(); task.cancel(with: .normalClosure, reason: nil) }

        func next() async throws -> LFMWire.Event {
            switch try await task.receive() {
            case .string(let s): return LFMWire.parse(s)
            case .data(let d): return LFMWire.parse(String(decoding: d, as: UTF8.self))
            @unknown default: return .other
            }
        }
        try await task.send(.string(LFMWire.sessionUpdate))
        while true { if case .sessionUpdated = try await next() { break } }
        for e in LFMWire.appendEvents(wave) { try await task.send(.string(e)) }
        try await task.send(.string(LFMWire.commit))
        let t0 = ContinuousClock.now
        try await task.send(.string(LFMWire.create))
        var first: Int?
        var bytes = 0
        loop: while true {
            switch try await next() {
            case .audio(let d):
                if first == nil { first = Int((ContinuousClock.now - t0) / .milliseconds(1)) }
                bytes += d.count
                await onAudio(d)
            case .done: break loop
            case .error(let m): throw NSError(domain: "LFM", code: 2, userInfo: [NSLocalizedDescriptionKey: m])
            default: continue
            }
        }
        return LFMTurnResult(firstAudioMs: first, audioSeconds: Double(bytes) / 2 / Double(LFMWire.outputRate))
    }
}

/// Plays the model's 24 kHz reply as it arrives. Its own engine: push-to-talk recorded with the plain mic, so nothing here needs the
/// echo canceller.
public final class LFMPlayer: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let fmt = AVAudioFormat(standardFormatWithSampleRate: Double(LFMWire.outputRate), channels: 1)!
    private let lock = NSLock()
    private var pending = 0, inputDone = false, drained: (@Sendable () -> Void)?

    public init() {}

    public func start() throws {
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: fmt)
        try engine.start()
        node.play()
    }

    public func enqueue(_ pcm16: Data) {
        let f = LFMWire.floats(fromPCM16: pcm16)
        guard !f.isEmpty, let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(f.count)) else { return }
        buf.frameLength = AVAudioFrameCount(f.count)
        f.withUnsafeBufferPointer { buf.floatChannelData![0].update(from: $0.baseAddress!, count: f.count) }
        lock.withLock { pending += 1 }
        node.scheduleBuffer(buf, completionCallbackType: .dataPlayedBack) { [weak self] _ in self?.played() }
    }

    /// No more audio is coming: `done` runs when everything queued has been heard.
    public func finish(_ done: @escaping @Sendable () -> Void) {
        let now: Bool = lock.withLock { inputDone = true; drained = done; return pending == 0 }
        if now { done() }
    }

    public func stop() {
        lock.withLock { drained = nil }
        node.stop()
        if engine.isRunning { engine.stop() }
    }

    private func played() {
        let cb: (@Sendable () -> Void)? = lock.withLock {
            pending -= 1
            guard pending == 0, inputDone else { return nil }
            let d = drained; drained = nil; return d
        }
        cb?()
    }
}
