import Foundation

/// The frame captured when he starts to speak (step C3). People look away while they finish a sentence, so the first
/// frame of an utterance is kept; once the message is sent it is used up and the next utterance captures a fresh one.
public struct FrameHold: Sendable {
    public private(set) var frame: JSONValue?
    public private(set) var at: Date?
    public private(set) var pending = false
    public init() {}

    /// True when a capture should start now (nothing held, none already running).
    public mutating func wantsCapture() -> Bool {
        guard frame == nil, !pending else { return false }
        pending = true
        return true
    }
    public mutating func store(_ f: JSONValue?, now: Date = Date()) {
        pending = false
        if frame == nil { frame = f; at = now }          // the first one wins
    }
    /// The frame for the message being sent, and the hold is cleared.
    public mutating func take() -> JSONValue? {
        defer { frame = nil; at = nil; pending = false }
        return frame
    }
}
