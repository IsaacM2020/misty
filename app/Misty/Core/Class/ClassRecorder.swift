import AVFoundation
import Foundation

/// An online class's two tracks, written to disk for the after-class pass: the Mac's sound (`class.m4a`, the
/// teacher and classmates) and the mic (`isaac.m4a`). 16 kHz mono AAC, about 15 MB an hour each, in
/// `~/Misty/class-audio/<session>/` with `tracks.json` holding when each track started, so both line up on one
/// timeline. Local only, never uploaded.
public final class ClassTrack: @unchecked Sendable {
    private let q = DispatchQueue(label: "misty.classtrack", qos: .utility)
    private let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    private var file: AVAudioFile?
    private var pcm: FileHandle?
    public let url: URL
    /// The same audio as plain 16 kHz 16-bit mono samples, written while the class runs. An m4a can't be opened until it
    /// is closed, so this is what the engine reads to correct the live transcript (and it survives a crash). `.start` holds
    /// the unix time of sample 0. Scratch: `close()` removes both once the m4a has been checked.
    public var pcmURL: URL { url.deletingPathExtension().appendingPathExtension("pcm") }
    public var startURL: URL { url.deletingPathExtension().appendingPathExtension("start") }
    public private(set) var startedAt: Date?
    public private(set) var frames: Int64 = 0
    private let now: @Sendable () -> Date
    private static let gapFrames: Int64 = 16000          // a hole of more than a second is filled, less is just jitter

    public init(url: URL, now: @escaping @Sendable () -> Date = Date.init) {
        self.url = url
        self.now = now
    }

    /// Called from the audio threads; the write happens off them. Nothing arrives while a class is paused (the lid is
    /// shut), so a gap is written as silence: the file stays on the class clock that `tracks.json` starts from.
    public func append(_ s: [Float]) {
        guard !s.isEmpty else { return }
        let at = now()
        q.async { [self] in
            if file == nil {
                let settings: [String: Any] = [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 16000,
                                               AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 32000]
                file = try? AVAudioFile(forWriting: url, settings: settings, commonFormat: .pcmFormatFloat32, interleaved: false)
                startedAt = at
                if FileManager.default.createFile(atPath: pcmURL.path, contents: nil, attributes: [.posixPermissions: 0o600]) {
                    pcm = try? FileHandle(forWritingTo: pcmURL)
                    try? String(format: "%.6f", at.timeIntervalSince1970).write(to: startURL, atomically: true, encoding: .utf8)
                }
            }
            guard let file, let startedAt else { return }
            let behind = Int64(at.timeIntervalSince(startedAt) * 16000) - frames
            if behind > Self.gapFrames { write(file, [Float](repeating: 0, count: Int(behind))) }
            write(file, s)
        }
    }

    private func write(_ file: AVAudioFile, _ s: [Float]) {
        guard let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(s.count)) else { return }
        buf.frameLength = AVAudioFrameCount(s.count)
        s.withUnsafeBufferPointer { buf.floatChannelData![0].update(from: $0.baseAddress!, count: s.count) }
        if (try? file.write(from: buf)) != nil { frames += Int64(s.count) }
        if let pcm {                                         // the same samples, plain, for the live corrections
            var d = Data(count: s.count * 2)
            d.withUnsafeMutableBytes { raw in
                let p = raw.bindMemory(to: Int16.self)
                for i in 0..<s.count { p[i] = Int16(max(-1, min(1, s[i])) * 32767) }
            }
            try? pcm.write(contentsOf: d)
        }
    }

    /// True when the finished m4a opens and holds audio.
    public static func m4aOpens(_ url: URL) -> Bool {
        guard let f = try? AVAudioFile(forReading: url) else { return false }
        return f.length > 0
    }

    /// Flushes and closes the file. Returns (started, seconds) once everything queued is written.
    public func close() -> (Date, Double)? {
        q.sync {
            file = nil                                       // AVAudioFile finishes the file when released
            try? pcm?.close()
            pcm = nil
            guard let startedAt else { return nil }
            if Self.m4aOpens(url) {                          // the real copy is good, so the scratch can go
                try? FileManager.default.removeItem(at: pcmURL)
                try? FileManager.default.removeItem(at: startURL)
            }
            return (startedAt, Double(frames) / 16000)
        }
    }
}

public final class ClassRecorder: @unchecked Sendable {
    public static var root: URL {
        let home = ProcessInfo.processInfo.environment["MISTY_HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Misty")
        return home.appendingPathComponent("class-audio")
    }

    public let session: String
    public let dir: URL
    public let mac: ClassTrack
    public let mic: ClassTrack

    public init?(session: String) {
        self.session = session
        dir = Self.root.appendingPathComponent(session)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
        } catch { return nil }
        mac = ClassTrack(url: dir.appendingPathComponent("class.m4a"))
        mic = ClassTrack(url: dir.appendingPathComponent("isaac.m4a"))
    }

    /// Closes both tracks and writes `tracks.json`: each file's start (unix seconds) and length.
    public func finish() {
        var out: [String: [String: Double]] = [:]
        if let (t, d) = mac.close() { out["class"] = ["started": t.timeIntervalSince1970, "seconds": d] }
        if let (t, d) = mic.close() { out["isaac"] = ["started": t.timeIntervalSince1970, "seconds": d] }
        if let data = try? JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("tracks.json"))
        }
    }
}
