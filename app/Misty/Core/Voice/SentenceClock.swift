import Foundation

/// Where each sentence sits on the speaker's timeline. Phonon hands over audio in chunks with no word timestamps, but every
/// chunk's sample count is exact, so the clock adds them up: a sentence starts when its first chunk starts playing (right away
/// if the player is idle, else after what is already scheduled) and ends when its last chunk runs out. All times are seconds on
/// one monotonic clock (`ProcessInfo.systemUptime`). Pure maths, no audio, so it is tested directly.
struct SentenceClock: Sendable {
    /// When the audio scheduled so far runs out.
    private(set) var playEnd: Double = 0
    /// This sentence's first audible moment, once its first chunk is in.
    private(set) var start: Double?
    /// Audio scheduled for this sentence, in seconds of real playback (already divided by the playback rate).
    private(set) var seconds: Double = 0

    mutating func begin() { start = nil; seconds = 0 }

    /// Silence: everything scheduled was dropped.
    mutating func reset() { playEnd = 0; start = nil; seconds = 0 }

    /// One chunk of `samples` at `sampleRate`, scheduled at `now`, played at `rate`x speed behind `latency` seconds of output delay.
    /// Returns this sentence's start time when this was its first chunk, else nil.
    @discardableResult
    mutating func add(samples: Int, sampleRate: Double, rate: Double, now: Double, latency: Double) -> Double? {
        let len = Double(samples) / sampleRate / max(rate, 0.1)
        let begin = max(now + latency, playEnd)
        playEnd = begin + len
        seconds += len
        guard start == nil else { return nil }
        start = begin
        return begin
    }
}
