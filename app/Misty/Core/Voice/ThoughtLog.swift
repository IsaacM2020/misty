import Foundation
import Observation

/// The live log he can open himself: every phrase the mic heard, each step she took deciding what it was, and what she did.
/// Memory only, on purpose. Nothing here is written to disk or sent anywhere: the room's words never touch a file (F5's rule),
/// and the log is gone when EDITH quits or he clears it.
@MainActor
@Observable
public final class ThoughtLog {
    public enum Verdict: String, Sendable {
        case working, answered, typed, ignored, noise
        public var label: String {
            switch self {
            case .working: "thinking"
            case .answered: "answered"
            case .typed: "typed"
            case .ignored: "ignored"
            case .noise: "noise"
            }
        }
    }

    public struct Step: Identifiable, Equatable, Sendable {
        public let id = UUID()
        public let at: Date
        public let text: String
    }

    public struct Entry: Identifiable, Equatable, Sendable {
        public let id: UUID
        public let at: Date
        public let heard: String
        public var steps: [Step] = []
        public var verdict: Verdict = .working
        public var result = ""
    }

    public static let keep = 200
    public private(set) var entries: [Entry] = []          // newest first

    public init() {}

    @discardableResult
    public func begin(_ heard: String, at: Date = Date()) -> UUID {
        let e = Entry(id: UUID(), at: at, heard: heard)
        entries.insert(e, at: 0)
        if entries.count > Self.keep { entries.removeLast(entries.count - Self.keep) }
        return e.id
    }

    public func step(_ id: UUID?, _ text: String) {
        guard let id, let i = entries.firstIndex(where: { $0.id == id }) else { return }
        entries[i].steps.append(Step(at: Date(), text: text))
    }

    public func finish(_ id: UUID?, _ verdict: Verdict, _ result: String = "") {
        guard let id, let i = entries.firstIndex(where: { $0.id == id }) else { return }
        entries[i].verdict = verdict
        entries[i].result = result
    }

    public func clear() { entries.removeAll() }
}
