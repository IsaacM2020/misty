import Foundation

/// The three things the voice tells the Slate about a line it is saying (see `SlateVM.timing`). Plain closures so
/// `VoiceVM` does not need to know the Slate.
public struct SlateTiming {
    /// Line `i`'s audio starts in this many seconds.
    public var start: @MainActor (Int, Double) -> Void
    /// Line `i` is fully generated: its audio ends in this many seconds, and is this many seconds long.
    public var end: @MainActor (Int, Double, Double) -> Void
    /// Line `i` has no voice (muted, no model): ink it at the fixed pace.
    public var silent: @MainActor (Int) -> Void
    /// The narration was cut short.
    public var stopped: @MainActor () -> Void
}
