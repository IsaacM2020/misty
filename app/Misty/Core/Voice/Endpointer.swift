import Foundation

/// Decides when Isaac starts and stops talking, from 16 kHz mono samples. Loudness against a moving noise floor:
/// speech starts after `startMs` of sound well above the floor and ends after `endMs` of quiet, so a phrase closes
/// even in a noisy room (the server's own endpoint never fired with a fan or traffic in the background).
public struct Endpointer: Sendable {
    public enum Event: Equatable, Sendable {
        case started
        case ended([Float])          // the whole phrase, with a little audio from just before it started
    }

    public static let rate = 16000
    static let frame = 320                         // 20 ms

    public var startMs = 120.0
    public var endMs = 650.0
    /// F2: the quiet after which Ears may try one early read of the phrase (see `Ears.maybeEarlyEnd`). Never under 300.
    public static let earliestEarlyEndMs = 300.0
    public static let earlyEndMs = max(earliestEarlyEndMs, 350.0)
    public var maxSeconds = 15.0                   // the ears' one encoder size
    /// Where a nonstop phrase is cut: under 15 s, because the model pads a 15.0 s phrase to 15.02 s and refuses it.
    public static let maxPhraseSeconds = 14.5
    public static let quietMinLevel: Float = 0.0015   // auto-gain is off, so a normal voice can sit near 0.003
    public static let speakingMinLevel: Float = 0.012   // while Misty talks: above her echo, below Isaac's voice
    public var minLevel: Float = quietMinLevel      // RMS below this is never speech, however quiet the room is
    /// While Misty talks: her echo comes back as loud as Isaac (0.05 RMS on the MacBook speakers), so a phrase only
    /// starts when he is clearly louder than the loudest echo of the last second.
    public var overEcho = false
    static let echoOver: Float = 1.6
    private var recent: [Float] = []               // last ~1 s of frame levels
    public var ratio: Float = 3.0                  // speech is at least this many times the noise floor (~10 dB)

    public private(set) var inSpeech = false
    public private(set) var floor: Float = 0.002
    public private(set) var level: Float = 0            // the last frame's RMS, for the voice log
    private var pending: [Float] = []              // samples not yet a whole frame
    private var preroll: [Float] = []              // the last ~500 ms before speech, so the first word isn't clipped
    private var phrase: [Float] = []
    private var loudRun = 0, quietRun = 0
    private var phrasePeak: Float = 0              // loudest frame of the phrase: quiet is judged against it too

    public init() {}

    /// Far-field: a teacher across a classroom sits far below close talk. The phrase max stays 15 s (encoder size).
    /// After `softSeconds` of a phrase, a short pause (100 ms, a gap between words) ends it: the ears drop words in
    /// long chunks, and a teacher rarely stops for the full `endMs`.
    public mutating func classTuned() { minLevel = 0.0008; ratio = 2.2; endMs = 600; softSeconds = 6 }
    public mutating func closeTalk() { minLevel = Self.quietMinLevel; ratio = 3.0; endMs = 650; softSeconds = nil }
    public var softSeconds: Double?

    /// How long the quiet run is right now (0 while he is loud or no phrase is open).
    public var quietMs: Double { inSpeech ? Double(quietRun) * 20 : 0 }

    /// The phrase so far (for live partial text while he is still talking).
    public var current: [Float] { phrase }

    public mutating func feed(_ samples: [Float]) -> [Event] {
        pending += samples
        var out: [Event] = []
        var i = 0
        while pending.count - i >= Self.frame {
            let f = Array(pending[i..<(i + Self.frame)])
            i += Self.frame
            if let e = step(f) { out.append(e) }
        }
        pending.removeFirst(i)
        return out
    }

    /// Drop everything (a session ended, or Misty is about to listen afresh).
    public mutating func reset() {
        inSpeech = false; pending = []; preroll = []; phrase = []; loudRun = 0; quietRun = 0; phrasePeak = 0
    }

    /// The "phrase" was steady noise (no words in it): drop it and take its loudness as the room's new floor.
    public mutating func dropAsNoise() {
        guard inSpeech else { return }
        let n = phrase.count / Self.frame
        if n > 0 {
            let levels = (0..<n).map { i -> Float in
                let f = phrase[(i * Self.frame)..<((i + 1) * Self.frame)]
                return (f.reduce(0) { $0 + $1 * $1 } / Float(Self.frame)).squareRoot()
            }.sorted()
            floor = levels[n / 2]
        }
        inSpeech = false; phrase = []; loudRun = 0; phrasePeak = 0
    }

    /// Close the phrase now (the words stopped changing but the room never went quiet enough).
    public mutating func forceEnd() -> [Float]? {
        guard inSpeech else { return nil }
        let done = phrase
        inSpeech = false; phrase = []; loudRun = 0; phrasePeak = 0
        return done
    }

    private func frames(_ ms: Double) -> Int { Int(ms / 20) }

    private mutating func step(_ f: [Float]) -> Event? {
        let rms = (f.reduce(0) { $0 + $1 * $1 } / Float(f.count)).squareRoot()
        level = rms
        recent.append(rms)
        if recent.count > 50 { recent.removeFirst(recent.count - 50) }
        var threshold = max(minLevel, floor * ratio)
        if overEcho && !inSpeech {
            // her loudest echo before the frames that would start a phrase (so his own voice doesn't raise the bar)
            let older = recent.dropLast(frames(startMs) + 1)
            threshold = max(threshold, (older.max() ?? 0) * Self.echoOver)
        }
        let loud = rms > threshold

        if !inSpeech {
            // the floor follows quiet quickly and noise slowly, so a steady fan becomes "quiet" within a few seconds
            floor = rms < floor ? floor * 0.9 + rms * 0.1 : min(floor * 1.002, max(rms, floor))
            preroll += f
            if preroll.count > Self.frame * 25 { preroll.removeFirst(preroll.count - Self.frame * 25) }
            loudRun = loud ? loudRun + 1 : 0
            guard loudRun >= frames(startMs) else { return nil }
            inSpeech = true
            quietRun = 0
            phrasePeak = rms
            phrase = preroll
            preroll = []
            return .started
        }

        phrase += f
        phrasePeak = max(phrasePeak, rms)
        // quiet = well under the room's speech threshold, or a tenth of how loud he was (a fan that started mid-phrase)
        quietRun = rms > max(threshold * 0.7, phrasePeak * 0.1) ? 0 : quietRun + 1
        let tooLong = phrase.count >= Int(min(maxSeconds, Self.maxPhraseSeconds) * Double(Self.rate))
        let pauseAfterSoft = softSeconds.map { phrase.count >= Int($0 * Double(Self.rate)) && quietRun >= frames(100) } ?? false
        guard quietRun >= frames(endMs) || tooLong || pauseAfterSoft else { return nil }
        let done = phrase
        inSpeech = false
        phrase = []
        loudRun = 0
        phrasePeak = 0
        return .ended(done)
    }
}
