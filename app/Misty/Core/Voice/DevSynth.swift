import Foundation
import AVFoundation

/// DEV ONLY (Isaac's own testing; hidden unless the misty.devTools flag is on). Adds the macOS system voices next to Phonon's own, behind the
/// same `SpeechSynth` protocol and the same `misty.voice` setting. A system voice shows up as "System: Name (lang)".
/// `Speaker.defaultSynth()` wraps Phonon in this only when `misty.devTools` is on.
public enum DevVoices {
    /// Hidden switch for Isaac's own Mac: `defaults write com.isaacmiranda.Misty misty.devTools -bool YES`. Off for everyone else.
    public static var enabled: Bool { UserDefaults.standard.bool(forKey: "misty.devTools") }
    public static let prefix = "System: "
    public static func isSystem(_ name: String) -> Bool { name.hasPrefix(prefix) }
    /// The English system voices, ready at once (no model to load), so the picker can fill while Phonon is still warming up.
    public static func systemVoices() -> [String] { SystemSynth.names() }
}

/// Phonon (or nothing) plus the system voices. Routes `speak` by whichever voice was picked last.
actor DevSynth: SpeechSynth {
    private let base: (any SpeechSynth)?
    private let system = SystemSynth()
    private var useSystem = false

    init(base: (any SpeechSynth)?) { self.base = base }

    func load() async throws -> [String] {
        let own = (try? await base?.load()) ?? []
        return own + (try await system.load())
    }

    func setVoice(_ name: String) async throws {
        if DevVoices.isSystem(name) {
            try await system.setVoice(name)
            useSystem = true
        } else {
            useSystem = false
            try await base?.setVoice(name)
        }
    }

    func speak(_ text: String, onAudio: @escaping @Sendable ([Float]) -> Void) async throws {
        if useSystem || base == nil { try await system.speak(text, onAudio: onAudio) }
        else { try await base?.speak(text, onAudio: onAudio) }
    }
}

/// AVSpeechSynthesizer rendered to buffers (no direct playback), converted to the mono 24 kHz Float32 the Speaker plays.
actor SystemSynth: SpeechSynth {
    private var identifier: String?

    func load() async throws -> [String] { Self.names() }

    nonisolated static func names() -> [String] { english().map(label) }

    func setVoice(_ name: String) async throws {
        guard let v = Self.english().first(where: { Self.label($0) == name }) else {
            throw NSError(domain: "SystemSynth", code: 1, userInfo: [NSLocalizedDescriptionKey: "no such system voice"])
        }
        identifier = v.identifier
    }

    func speak(_ text: String, onAudio: @escaping @Sendable ([Float]) -> Void) async throws {
        let utterance = AVSpeechUtterance(string: text)
        if let identifier { utterance.voice = AVSpeechSynthesisVoice(identifier: identifier) }
        let synth = Unsafe(AVSpeechSynthesizer())
        let utt = Unsafe(utterance)
        let target = AVAudioFormat(standardFormatWithSampleRate: Speaker.sampleRate, channels: 1)!
        let done = Box(false)
        let converter = Box<AVAudioConverter?>(nil)
        try await withTaskCancellationHandler {
            await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                @Sendable func finish() {
                    let first = done.withLock { d -> Bool in if d { return false }; d = true; return true }
                    if first { c.resume() }
                }
                synth.v.write(utt.v) { buffer in
                    guard let pcm = buffer as? AVAudioPCMBuffer, pcm.frameLength > 0 else { finish(); return }   // empty buffer = end
                    let conv: AVAudioConverter? = converter.withLock { cur in
                        if cur == nil || cur!.inputFormat != pcm.format { cur = AVAudioConverter(from: pcm.format, to: target) }
                        return cur
                    }
                    guard let conv else { return }
                    let ratio = target.sampleRate / pcm.format.sampleRate
                    let cap = AVAudioFrameCount(Double(pcm.frameLength) * ratio) + 64
                    guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: cap) else { return }
                    let fed = Box(false)
                    var err: NSError?
                    conv.convert(to: out, error: &err) { _, status in
                        if fed.withLock({ let was = $0; $0 = true; return was }) { status.pointee = .noDataNow; return nil }
                        status.pointee = .haveData
                        return pcm
                    }
                    if err == nil, out.frameLength > 0, let p = out.floatChannelData?[0] {
                        onAudio(Array(UnsafeBufferPointer(start: p, count: Int(out.frameLength))))
                    }
                }
            }
        } onCancel: {
            synth.v.stopSpeaking(at: .immediate)
        }
        try Task.checkCancellation()
    }

    private struct Unsafe<T>: @unchecked Sendable { let v: T; init(_ v: T) { self.v = v } }
    private final class Box<T>: @unchecked Sendable {
        private let lock = NSLock()
        private var value: T
        init(_ v: T) { value = v }
        func withLock<R>(_ f: (inout T) -> R) -> R { lock.lock(); defer { lock.unlock() }; return f(&value) }
    }

    private nonisolated static func english() -> [AVSpeechSynthesisVoice] {
        AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language.hasPrefix("en") }
            .sorted { ($0.language, $0.name) < ($1.language, $1.name) }
    }

    private nonisolated static func label(_ v: AVSpeechSynthesisVoice) -> String { "\(DevVoices.prefix)\(v.name) (\(v.language))" }
}

/// Dev only: `defaults write com.isaacmiranda.Misty misty.ttsDump -string /some/folder` saves each spoken sentence as a wav
/// there (24 kHz mono), so a pronunciation can be checked without ears. Needs misty.devTools on too.
final class DevDump: @unchecked Sendable {
    private let lock = NSLock()
    private var samples: [Float] = []
    static var folder: URL? {
        guard DevVoices.enabled, let p = UserDefaults.standard.string(forKey: "misty.ttsDump"), !p.isEmpty else { return nil }
        return URL(fileURLWithPath: p)
    }
    func add(_ chunk: [Float]) { lock.lock(); samples += chunk; lock.unlock() }
    func write(to dir: URL, name: String) {
        lock.lock(); let w = samples; lock.unlock()
        var d = Data()
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        let rate = UInt32(Speaker.sampleRate), n = UInt32(w.count * 2)
        d.append(contentsOf: Array("RIFF".utf8)); u32(36 + n); d.append(contentsOf: Array("WAVEfmt ".utf8))
        u32(16); u16(1); u16(1); u32(rate); u32(rate * 2); u16(2); u16(16)
        d.append(contentsOf: Array("data".utf8)); u32(n)
        for x in w { u16(UInt16(bitPattern: Int16(max(-1, min(1, x)) * 32767))) }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try? d.write(to: dir.appendingPathComponent(name))
    }
}
