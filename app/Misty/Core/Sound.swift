import AVFoundation

/// Misty's key sounds. They are synthesised here (no sound files, no licences): a round pip when a button goes down,
/// a softer tick when it comes back up, a bubbly pop when something commits, and a two-note chirp when something is done.
/// Off with `defaults write com.isaacmiranda.Misty uiSounds -bool false`.
@MainActor
final class Sound {
    static let shared = Sound()

    enum Cue: CaseIterable { case down, up, thunk, done, tick, turn, wake }

    private let engine = AVAudioEngine()
    private var players: [AVAudioPlayerNode] = []
    private var buffers: [Cue: AVAudioPCMBuffer] = [:]
    private var next = 0
    private var ready = false
    private static let rate: Double = 44100

    private var enabled: Bool { UserDefaults.standard.object(forKey: "uiSounds") as? Bool ?? true }

    private init() {
        guard let format = AVAudioFormat(standardFormatWithSampleRate: Self.rate, channels: 1) else { return }
        for cue in Cue.allCases { buffers[cue] = Self.render(cue, format: format) }
        for _ in 0..<4 {
            let p = AVAudioPlayerNode()
            engine.attach(p)
            engine.connect(p, to: engine.mainMixerNode, format: format)
            players.append(p)
        }
        engine.mainMixerNode.outputVolume = 0.5
        ready = true
    }

    func play(_ cue: Cue) {
        guard ready, enabled, let buffer = buffers[cue] else { return }
        if !engine.isRunning { try? engine.start() }
        guard engine.isRunning else { return }
        let p = players[next]
        next = (next + 1) % players.count
        p.scheduleBuffer(buffer, at: nil, options: .interrupts)
        if !p.isPlaying { p.play() }
    }

    // MARK: synthesis

    private static func render(_ cue: Cue, format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let seconds: Double
        switch cue {
        case .down: seconds = 0.03
        case .up: seconds = 0.025
        case .thunk: seconds = 0.16
        case .done: seconds = 0.22
        case .tick: seconds = 0.05
        case .turn: seconds = 0.5
        case .wake: seconds = 0.26
        }
        let n = AVAudioFrameCount(seconds * rate)
        guard let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: n), let out = buf.floatChannelData?[0] else { return nil }
        buf.frameLength = n
        var seed: UInt32 = 0x1234_5678
        func noise() -> Float {                      // small deterministic white noise, so the click is the same every time
            seed = seed &* 1664525 &+ 1013904223
            return Float(Int32(bitPattern: seed)) / Float(Int32.max)
        }
        for i in 0..<Int(n) {
            let t = Double(i) / rate
            var s: Float = 0
            switch cue {
            case .down:
                let f = 640 + 360 * exp(-t / 0.006)          // a round little pip that falls in pitch
                s = Float(sin(2 * .pi * f * t) * exp(-t / 0.009)) * 0.55
            case .up:
                s = Float(sin(2 * .pi * 520 * t) * exp(-t / 0.006)) * 0.3
            case .thunk:
                let f = 240 + 520 * (1 - exp(-t / 0.035))    // a bubble pop: pitch bends up as it blooms
                s = Float(sin(2 * .pi * f * t) * exp(-t / 0.05)) * 0.7 + noise() * Float(exp(-t / 0.0015)) * 0.12
            case .done:
                let a = sin(2 * .pi * 880 * t) * exp(-t / 0.05)
                let b = t > 0.07 ? sin(2 * .pi * 1320 * (t - 0.07)) * exp(-(t - 0.07) / 0.06) : 0
                s = Float(a * 0.35 + b * 0.35)
            case .tick:                                  // a soft felt-tip tap as a line starts writing
                s = Float(sin(2 * .pi * 1900 * t) * exp(-t / 0.004)) * 0.12 + noise() * Float(exp(-t / 0.003)) * 0.05
            case .turn:                                  // "your turn": two warm marimba notes, a rising fifth
                func mallet(_ f: Double, _ t0: Double) -> Double {
                    guard t >= t0 else { return 0 }
                    let u = t - t0
                    return (sin(2 * .pi * f * u) + 0.25 * sin(2 * .pi * f * 4 * u) * exp(-u / 0.02)) * exp(-u / 0.16)
                }
                s = Float(mallet(523.25, 0) * 0.32 + mallet(783.99, 0.11) * 0.32)
            case .wake:                                  // "I heard you": one short soft bell, a little under the other cues
                let a = sin(2 * .pi * 987.77 * t) * exp(-t / 0.07)
                let b = 0.3 * sin(2 * .pi * 987.77 * 2.76 * t) * exp(-t / 0.03)
                let fade = min(1, t / 0.004)                 // no click at the start
                s = Float((a + b) * fade) * 0.22
            }
            out[i] = s
        }
        return buf
    }
}
