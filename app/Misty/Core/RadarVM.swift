import Foundation
import Observation

/// What Misty cares about, as the engine's `cares.list` says. View only: she changes it when he tells her in chat.
public struct Care: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var key: String
    public var kind: String
    public var text: String
    public var weight: Int
    public var level: String
}

/// One thing she is keeping an eye on, with the newest thing it found.
public struct Watch: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var text: String
    public var source: String
    public var everyMin: Int
    public var until: String
    public var checks: Int
    public var lastError: String?
    public var lastChecked: String?
    public var latest: String?
    public var latestSource: String?
}

/// One line she told him on her own (Told you), with the thumb he gave it, if any.
public struct Told: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var at: Double
    public var topicText: String
    public var said: String
    public var sourceName: String?
    public var how: String
    public var feedback: String?
}

/// One thing she has learned about when to tell him things (from his feedback or from him telling her).
public struct Lesson: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var n: Int
    public var text: String
    public var source: String
}

/// One way she is with him ("Call him boss.", "Dry and understated."). `origin` is "default" until he says one himself.
public struct Way: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var key: String
    public var kind: String
    public var text: String
    public var origin: String
    public var isDefault: Bool { origin == "default" }
}

/// One thing she did for him (a move), newest first. `verified` nil means it never ran (he said no, or it was refused).
public struct DidMove: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int
    public var at: Double
    public var name: String
    public var what: String
    public var verified: Bool?
    public var undoable: Bool
    public var protocolRun: String?
    public var isVerified: Bool { verified == true }
    public var title: String { name.replacingOccurrences(of: "_", with: " ") }
}

/// One of his protocols, in plain words.
public struct ProtocolRow: Decodable, Equatable, Sendable, Identifiable {
    public var key: String
    public var triggers: [String]
    public var steps: [String]
    public var runs: Int
    public var id: String { key }
}

/// What is still to do on a course: today's topic, or the next planned day's.
public struct LearningNext: Decodable, Equatable, Sendable {
    public var topic: String
    public var day: String
    public var kind: String
}

/// One course he is on, counted by the engine from its rows (`teach.overview`).
public struct LearningCourse: Decodable, Equatable, Sendable, Identifiable {
    public var course: String
    public var subject: String
    public var goal: String
    public var by: String?
    public var daysLeft: Int?
    public var status: String
    public var topics: Int
    public var solid: Int
    public var shaky: Int
    public var due: Int
    public var next: LearningNext?
    public var id: String { course }
    /// How much of the course is solid or better, 0 to 1.
    public var fraction: Double { topics > 0 ? min(1, Double(solid) / Double(topics)) : 0 }
}

/// A topic in the shaky list or the due list. `why` is only there for shaky ones.
public struct LearningTopic: Decodable, Equatable, Sendable, Identifiable {
    public var topic: String
    public var subject: String
    public var level: String
    public var why: String?
    public var id: String { subject + "/" + topic }
}

public struct LearningProbe: Decodable, Equatable, Sendable, Identifiable {
    public var topic: String
    public var line: String
    public var id: String { topic }
}

public struct LearningDrills: Decodable, Equatable, Sendable {
    public var days: Int
    public var count: Int
    public var right: Int
    public var wrong: Int
}

public struct LearningMistakes: Decodable, Equatable, Sendable {
    public var total: Int
    public var week: Int
    public var retired: Int
    public var flagged: Int
    public var pattern: String
}

/// The whole Learning view. View only: he changes a course by telling her.
public struct Learning: Decodable, Equatable, Sendable {
    public var courses: [LearningCourse]
    public var shaky: [LearningTopic]
    public var due: [LearningTopic]
    public var probes: [LearningProbe]
    public var drills: LearningDrills
    public var mistakes: LearningMistakes
    /// Nothing to show: no course, nothing shaky or due, no drill this week, no mistake saved.
    public var isEmpty: Bool {
        courses.isEmpty && shaky.isEmpty && due.isEmpty && probes.isEmpty && drills.count == 0 && mistakes.total == 0
    }
}

/// The plain lines under the Learning cards. Kept here, not in the view, so a test can read them.
public enum LearningWords {
    public static func when(_ c: LearningCourse) -> String {
        guard let d = c.daysLeft, let by = c.by else { return "no date" }
        if d < 0 { return "\(by), \(-d) day\(d == -1 ? "" : "s") over" }
        if d == 0 { return "\(by), today" }
        return "\(by), \(d) day\(d == 1 ? "" : "s") left"
    }

    public static func next(_ n: LearningNext) -> String {
        n.kind == "review" ? "Next: a quick drill on \(n.topic), \(n.day)" : "Next: \(n.topic), \(n.day)"
    }

    public static func drills(_ d: LearningDrills) -> String {
        "\(d.count) drill\(d.count == 1 ? "" : "s") in \(d.days) days: \(d.right) right, \(d.wrong) wrong."
    }

    public static func mistakes(_ m: LearningMistakes) -> String {
        var s = "\(m.total) mistake\(m.total == 1 ? "" : "s") saved, \(m.week) this week"
        if m.retired > 0 { s += ", \(m.retired) learned for good" }
        return s + "."
    }
}

@MainActor
@Observable
public final class RadarVM {
    public private(set) var cares: [Care] = []
    public private(set) var watching: [Watch] = []
    public private(set) var told: [Told] = []
    public private(set) var lessons: [Lesson] = []
    public private(set) var ways: [Way] = []
    public private(set) var privateApps: [String] = []
    public private(set) var did: [DidMove] = []
    public private(set) var protocols: [ProtocolRow] = []
    public private(set) var learning: Learning?
    public private(set) var held = 0
    public private(set) var loaded = false
    private let api: EngineAPI

    public init(api: EngineAPI) { self.api = api }

    /// The order the page shows them in, with the heading for each.
    public nonisolated static let groups: [(kind: String, title: String)] = [
        ("goal", "Goals"), ("interest", "Interests"), ("person", "People"), ("other", "Other")]

    public func care(in kind: String) -> [Care] { cares.filter { $0.kind == kind } }

    public func load() async {
        struct P: Encodable, Sendable {}
        struct R: Decodable, Sendable { var cares: [Care] }
        struct W: Decodable, Sendable { var follows: [Watch] }
        do {
            let r: R = try await api.call("cares.list", P())
            cares = r.cares
        } catch {}
        do {
            let w: W = try await api.call("follows.list", P())
            watching = w.follows
        } catch {}
        do {
            struct T: Decodable, Sendable { var told: [Told]; var held: [JSONValue] }
            struct L: Encodable, Sendable { var limit = 20 }
            let t: T = try await api.call("told.list", L())
            told = t.told
            held = t.held.count
        } catch {}
        do {
            struct L: Decodable, Sendable { var lessons: [Lesson] }
            let l: L = try await api.call("lessons.list", P())
            lessons = l.lessons
        } catch {}
        do {
            struct W: Decodable, Sendable { var ways: [Way] }
            let w: W = try await api.call("ways.list", P())
            ways = w.ways
        } catch {}
        do {
            struct M: Decodable, Sendable { var moves: [DidMove] }
            struct L: Encodable, Sendable { var limit = 15 }
            let m: M = try await api.call("moves.list", L())
            did = m.moves
        } catch {}
        do {
            struct R: Decodable, Sendable { var protocols: [ProtocolRow] }
            let r: R = try await api.call("protocols.list", P())
            protocols = r.protocols
        } catch {}
        do {
            let l: Learning = try await api.call("teach.overview", P())
            learning = l.isEmpty ? nil : l
        } catch {}
        do {
            struct A: Decodable, Sendable { var name: String }
            struct R: Decodable, Sendable { var apps: [A] }
            let r: R = try await api.call("privacy.list", P())
            privateApps = r.apps.map(\.name)
        } catch {}
        loaded = true
    }

    /// A thumb on one line. The engine keeps it (and, for a thumbs-down, what she knew at that moment).
    public func rate(_ id: String, _ value: String) async {
        struct P: Encodable, Sendable { var id: String; var value: String }
        struct R: Decodable, Sendable { var ok: Bool }
        guard let r: R = try? await api.call("told.rate", P(id: id, value: value)), r.ok,
              let i = told.firstIndex(where: { $0.id == id }) else { return }
        told[i].feedback = value
    }
}
