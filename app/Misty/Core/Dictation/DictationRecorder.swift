import AVFoundation

/// Records the mic as 16 kHz mono Float32 into memory, through `AudioIO`, the same capture path live mode uses but
/// without voice processing: Misty makes no sound while you dictate, and voice processing would turn the volume of
/// everything else down. Its own engine instance, so dictation and live mode never share one. If the audio route changes mid-sentence (AirPods
/// connect), the capture is restarted and the words so far are kept.
final class DictationRecorder: @unchecked Sendable {
    /// Raw loudness (RMS) of each chunk, off the main thread. The pill maps it to a decibel scale.
    var onLevel: (@Sendable (Float) -> Void)?
    /// The samples of each chunk, off the main thread (the wake word's turn watcher). Set before `start`.
    var onChunk: (@Sendable ([Float]) -> Void)?

    static let maxSeconds = 300
    private let io = AudioIO(voiceProcessing: false)
    private let lock = NSLock()
    private var samples: [Float] = []
    private var observer: NSObjectProtocol?
    private var chunks = 0
    private(set) var running = false
    /// Loudest chunk of the last recording, for the voice log: 0 means the mic gave silence.
    private(set) var peakRMS: Float = 0
    var report: String { lock.lock(); defer { lock.unlock() }; return "chunks=\(chunks)" }

    /// `prefill` is audio heard just before the mic opened (the words after "Edith"): it becomes the start of the recording.
    func start(prefill: [Float] = []) throws {
        guard !running else { return }
        lock.lock(); samples = prefill; samples.reserveCapacity(16000 * 30 + prefill.count); peakRMS = 0; chunks = 0; lock.unlock()
        running = true
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: .main) { [weak self] n in
            guard let self, self.running, n.object as AnyObject? === self.io.engine else { return }
            VoiceLog.write("dictation: audio route changed, restarting the mic")
            self.io.stop()
            do { try self.capture() } catch { VoiceLog.write("dictation: could not restart the mic \(error)") }
        }
        do { try capture() } catch { running = false; removeObserver(); throw error }
    }

    /// V10: while the mouse is over the pill the live read takes the whole recording, so the open card shows all of it.
    var wantAll: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _wantAll }
        set { lock.lock(); _wantAll = newValue; lock.unlock() }
    }
    private var _wantAll = false

    /// V8: the last `seconds` heard so far, for the live words (all of it while `wantAll`). The recording keeps going.
    func snapshot(lastSeconds seconds: Double = 12) -> [Float] {
        lock.lock(); defer { lock.unlock() }
        if _wantAll { return samples }
        let n = Int(seconds * 16000)
        return samples.count <= n ? samples : Array(samples.suffix(n))
    }

    /// Stop and hand back everything heard.
    func stop() -> [Float] {
        guard running else { return [] }
        running = false
        removeObserver()
        io.stop()
        lock.lock(); defer { lock.unlock() }
        return samples
    }

    private func removeObserver() { if let o = observer { NotificationCenter.default.removeObserver(o); observer = nil } }

    private func capture() throws {
        try io.start { [weak self] chunk in
            guard let self, !chunk.isEmpty else { return }
            let rms = (chunk.reduce(Float(0)) { $0 + $1 * $1 } / Float(chunk.count)).squareRoot()
            self.lock.lock()
            self.chunks += 1
            if self.samples.count < Self.maxSeconds * 16000 { self.samples.append(contentsOf: chunk) }
            if rms > self.peakRMS { self.peakRMS = rms }
            self.lock.unlock()
            self.onLevel?(rms)
            self.onChunk?(chunk)
        }
    }
}
