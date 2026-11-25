import Foundation

/// Live transcript v2: what the engine hands back after Phonon-2.5 English has read a finished stretch of a class again.
public struct ClassChange: Codable, Equatable, Sendable {
    public var removed: [Int]               // preview lines that were replaced
    public var added: [ClassSegment]        // the corrected lines, same track label and class time
}

public struct ClassCorrection: Codable, Equatable, Sendable {
    public var changes: [ClassChange]
    public var upTo: Double                 // class seconds corrected so far
    public var busy: Bool? = nil            // the engine was already running a round
}

public enum ClassLines {
    /// The transcript with each change applied: replaced preview lines out, corrected lines in, in class-time order
    /// (the same order the engine keeps them in).
    public static func apply(_ segs: [ClassSegment], _ changes: [ClassChange]) -> [ClassSegment] {
        guard !changes.isEmpty else { return segs }
        let gone = Set(changes.flatMap(\.removed))
        let all = segs.filter { !gone.contains($0.id) } + changes.flatMap(\.added)
        return all.enumerated().sorted {
            ($0.element.tStart, $0.element.id, $0.offset) < ($1.element.tStart, $1.element.id, $1.offset)
        }.map(\.element)
    }

    /// A preview phrase whose middle lies before `upTo` belongs to a stretch already corrected: it would only be a
    /// second copy, so it is not stored. (The engine hands lines to windows by their middle too.)
    public static func isCovered(tStart: Double, tEnd: Double, upTo: Double) -> Bool {
        upTo > 0 && (tStart + tEnd) / 2 < upTo
    }
}
