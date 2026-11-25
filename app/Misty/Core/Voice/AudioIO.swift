import AVFoundation
import Foundation

/// The one audio engine for voice. The mic runs with macOS voice processing (echo cancellation), and Misty's speech
/// plays through this same engine, so the canceller knows her voice and Isaac can talk over her, even on speakers.
public final class AudioIO: @unchecked Sendable {
    public let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private let out = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    private(set) public var running = false
    private var peak: Float = 0                       // loudest sample since the last `takePeak()`, for the voice log

    public func takePeak() -> Float { defer { peak = 0 }; return peak }

    /// Echo cancellation is for live mode, where Misty talks and listens at once. Dictation never plays sound, so it
    /// records the plain mic: macOS turns every other app's volume down for as long as voice processing is on.
    /// Set before `start`. A class turns it off: with it on, macOS silences the Mac's-sound tap in the same process
    /// (its callbacks stop dead the moment the mic opens), and it also cancels a class played on the speakers.
    public var voiceProcessing: Bool

    public init(voiceProcessing: Bool = true) { self.voiceProcessing = voiceProcessing }

    /// When the Mac's audio route changes under a running mic (AirPods or a headset connect, a call app takes the
    /// device, the input goes from 1 to 3 channels), AVAudioEngine stops itself and only posts a notification. With
    /// this on, `AudioIO` reopens the mic by itself and keeps feeding the same `onSamples`. A class turns it on (its
    /// mic must never go quiet for an hour unseen); `DictationRecorder` restarts on its own, so it stays off.
    /// Set before `start`.
    public var restartOnRouteChange = false
    private var handler: (@Sendable ([Float]) -> Void)?
    private var routeObserver: NSObjectProtocol?

    /// Switch voice processing on just before the mic opens (the first switch takes about a second). It doesn't open
    /// the mic. Other apps' audio is dipped as little as macOS allows (about 4 dB) while live mode is on.
    public func prepare() {
        guard voiceProcessing, !engine.isRunning else { return }       // voice processing can only be switched on while stopped
        let input = engine.inputNode
        if !input.isVoiceProcessingEnabled { try? input.setVoiceProcessingEnabled(true) }
        // auto-gain off: it lifted far-away room talk and her own echo up to the level of Isaac at the keyboard
        input.isVoiceProcessingAGCEnabled = false
        input.voiceProcessingOtherAudioDuckingConfiguration =
            AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
    }

    /// Start the mic. `onSamples` gets 16 kHz mono Float32, about every 20-100 ms, off the main thread.
    public func start(onSamples: @escaping @Sendable ([Float]) -> Void) throws {
        guard !running else { return }
        try open(onSamples)
        handler = onSamples
        if restartOnRouteChange { watchRoute() }
    }

    private func open(_ onSamples: @escaping @Sendable ([Float]) -> Void) throws {
        guard !running else { return }
        let input = engine.inputNode
        if engine.isRunning { engine.stop() }
        if !voiceProcessing, input.isVoiceProcessingEnabled { try? input.setVoiceProcessingEnabled(false) }
        prepare()
        let inFormat = input.outputFormat(forBus: 0)
        // With voice processing on, the input arrives as several identical channels (9 on a MacBook), and
        // AVAudioConverter's downmix of that comes out silent. So take channel 0 ourselves, then resample one channel.
        guard inFormat.sampleRate > 0, inFormat.commonFormat == .pcmFormatFloat32,
              let mono = AVAudioFormat(standardFormatWithSampleRate: inFormat.sampleRate, channels: 1),
              let conv = AVAudioConverter(from: mono, to: out) else {
            throw NSError(domain: "AudioIO", code: 1, userInfo: [NSLocalizedDescriptionKey: "no microphone: \(inFormat)"])
        }
        converter = conv
        let outFormat = out
        input.installTap(onBus: 0, bufferSize: 1024, format: inFormat) { [weak self] buf, _ in
            guard let src = buf.floatChannelData, buf.frameLength > 0,
                  let one = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: buf.frameLength) else { return }
            one.frameLength = buf.frameLength
            one.floatChannelData![0].update(from: src[0], count: Int(buf.frameLength))
            let ratio = outFormat.sampleRate / mono.sampleRate
            let cap = AVAudioFrameCount(Double(buf.frameLength) * ratio + 32)
            guard let dst = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: cap) else { return }
            var fed = false
            var err: NSError?
            conv.convert(to: dst, error: &err) { _, status in
                if fed { status.pointee = .noDataNow; return nil }
                fed = true
                status.pointee = .haveData
                return one
            }
            guard err == nil, dst.frameLength > 0, let p = dst.floatChannelData else { return }
            let samples = Array(UnsafeBufferPointer(start: p[0], count: Int(dst.frameLength)))
            let loud = samples.reduce(Float(0)) { max($0, abs($1)) }
            if let self, loud > self.peak { self.peak = loud }
            onSamples(samples)
        }
        engine.prepare()
        do { try engine.start() } catch {
            input.removeTap(onBus: 0)                    // a failed reopen must not leave a tap behind: the next try installs its own
            throw error
        }
        running = true
    }

    /// A sound file as 16 kHz mono Float32, for the silent class test (MISTY_CLASS_WAV): nothing is played aloud.
    public static func fileSamples(_ path: String) throws -> [Float] {
        let f = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        let inF = f.processingFormat
        guard let src = AVAudioPCMBuffer(pcmFormat: inF, frameCapacity: AVAudioFrameCount(f.length)),
              let mono = AVAudioFormat(standardFormatWithSampleRate: inF.sampleRate, channels: 1),
              let out = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false),
              let conv = AVAudioConverter(from: mono, to: out) else { return [] }
        try f.read(into: src)
        guard let one = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: src.frameLength),
              let dst = AVAudioPCMBuffer(pcmFormat: out, frameCapacity: AVAudioFrameCount(Double(src.frameLength) * 16000 / inF.sampleRate + 64))
        else { return [] }
        one.frameLength = src.frameLength
        one.floatChannelData![0].update(from: src.floatChannelData![0], count: Int(src.frameLength))
        var fed = false
        var err: NSError?
        conv.convert(to: dst, error: &err) { _, status in
            if fed { status.pointee = .endOfStream; return nil }
            fed = true
            status.pointee = .haveData
            return one
        }
        if let err { throw err }
        return Array(UnsafeBufferPointer(start: dst.floatChannelData![0], count: Int(dst.frameLength)))
    }

    /// Echo cancellation on a running mic, switched only when it changes. Voice processing can only flip while the engine is
    /// stopped, so this stops, flips and reopens with the same handler (a gap of a few hundred ms). Live mode keeps it off
    /// while she only listens: it cost about a quarter of a CPU core all day with nothing to cancel.
    public func setEchoCancel(_ on: Bool) {
        voiceProcessing = on
        guard running, let h = handler, engine.inputNode.isVoiceProcessingEnabled != on else { return }
        halt()
        do { try open(h) } catch {
            VoiceLog.write("echo cancel \(on) reopen failed \(error), plain mic")
            voiceProcessing = false
            try? open(h)
        }
        VoiceLog.write("echo cancel \(on ? "on" : "off")")
    }

    /// Stopped on purpose: no restart after this, even if a route change is still being retried.
    public func stop() {
        unwatchRoute()
        handler = nil
        halt()
    }

    private func halt() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
        // Voice processing off again: left on, macOS keeps every other app's volume down until Misty quits.
        if engine.inputNode.isVoiceProcessingEnabled { try? engine.inputNode.setVoiceProcessingEnabled(false) }
    }

    // MARK: the route changed under a running mic

    private func watchRoute() {
        guard routeObserver == nil else { return }
        routeObserver = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: .main) { [weak self] n in
            guard let self, RouteRestart.applies(running: self.running, optedIn: self.restartOnRouteChange,
                                                 from: n.object as AnyObject?, mine: self.engine) else { return }
            VoiceLog.write("mic: audio route changed, reopening the mic")
            RouteRestart.run(stop: { [weak self] in self?.halt() },
                             start: { [weak self] in
                                 guard let self, let h = self.handler else { return }      // stopped on purpose meanwhile
                                 try self.open(h)
                             },
                             retry: { after, again in DispatchQueue.main.asyncAfter(deadline: .now() + after, execute: again) },
                             log: { VoiceLog.write($0) })
        }
    }

    private func unwatchRoute() {
        if let o = routeObserver { NotificationCenter.default.removeObserver(o); routeObserver = nil }
    }

    public static var micAllowed: Bool { AVCaptureDevice.authorizationStatus(for: .audio) == .authorized }

    public static func askMic() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .audio)
        default: return false
        }
    }
}

/// What to do when AVAudioEngine stops itself because the audio route changed: stop the dead engine, open the mic
/// again, and if the device is still settling (AirPods mid-switch) try again after a growing wait. Pure logic, so it
/// is tested without a microphone (`AudioRouteRestartTests`).
enum RouteRestart {
    /// Waits before each retry; after the last one it gives up (about 7 s in all).
    static let delays: [TimeInterval] = [0.5, 1, 2, 4]

    /// Only a running mic that opted in, on a notification from its own engine.
    static func applies(running: Bool, optedIn: Bool, from: AnyObject?, mine: AnyObject) -> Bool {
        running && optedIn && from === mine
    }

    static func run(stop: () -> Void, start: @escaping () throws -> Void,
                    retry: @escaping (TimeInterval, @escaping () -> Void) -> Void, log: @escaping (String) -> Void) {
        stop()
        attempt(0, start: start, retry: retry, log: log)
    }

    private static func attempt(_ n: Int, start: @escaping () throws -> Void,
                                retry: @escaping (TimeInterval, @escaping () -> Void) -> Void, log: @escaping (String) -> Void) {
        do {
            try start()
            log("mic: restarted after a route change (try \(n + 1))")
        } catch {
            guard n < delays.count else { return log("mic: gave up reopening after a route change: \(error)") }
            log("mic: reopen failed (\(error)), trying again in \(delays[n]) s")
            retry(delays[n]) { attempt(n + 1, start: start, retry: retry, log: log) }
        }
    }
}
