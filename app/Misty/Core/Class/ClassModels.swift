import Foundation

/// The engine's class.* JSON (snake_case on the wire, camelCase here).
public struct ClassGap: Codable, Equatable, Sendable {
    public var from: Double
    public var to: Double?
    public var reason: String
}

public struct ClassPoint: Codable, Equatable, Sendable {
    public var text: String
    public var seg: [Int]
    public var mine: Bool?
}

public struct ClassSection: Codable, Equatable, Sendable {
    public var heading: String
    public var points: [ClassPoint]
}

public struct ClassRemember: Codable, Equatable, Sendable {
    public var kind: String
    public var text: String
    public var seg: [Int]
}

public struct ClassActionItem: Codable, Equatable, Sendable {
    public var id: String
    public var text: String
    public var kind: String
    public var due: String?
    public var dueText: String
    public var seg: [Int]
}

public struct ClassNotes: Codable, Equatable, Sendable {
    public var summary: String
    public var sections: [ClassSection]
    public var remember: [ClassRemember]
    public var actionItems: [ClassActionItem]
    public var gaps: [ClassGap]
    public var lost: [ClassPoint]? = nil        // where he got lost, each backed by a segment (notes before 3 Nov have none)
    public var nextStep: ClassPoint? = nil      // the one thing to do next
}

public struct ClassSession: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var subject: String
    public var title: String
    public var startedAt: Double
    public var endedAt: Double?
    public var status: String          // live | paused | ended | noting | noted | too_little | failed
    public var hisNotes: String
    public var notes: ClassNotes?
    public var gaps: [ClassGap]
    public var pruned: Int
    public var mode: String? = nil     // "online" (the teacher is the Mac's sound) | "room" (in person)
    public var cost: Double? = nil     // USD paid for this class's notes, every try (the transcript pass is local, free)

    public var isOnline: Bool { mode == "online" }
}

public struct ClassSegment: Codable, Equatable, Sendable, Identifiable {
    public var id: Int
    public var tStart: Double
    public var tEnd: Double
    public var source: String
    public var text: String
}

public struct ClassAction: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var text: String
    public var kind: String
    public var due: String?
    public var dueText: String
    public var segId: Int?
    public var priorityId: String?
    public var done: Int
}

public struct ClassDetail: Codable, Equatable, Sendable {
    public var session: ClassSession
    public var segments: [ClassSegment]
    public var actions: [ClassAction]
}

public struct ClassCite: Codable, Equatable, Sendable {
    public var session: String
    public var seg: Int
    public var tStart: Double
}

public struct ClassAnswer: Codable, Equatable, Sendable {
    public var answer: String
    public var cites: [ClassCite]
}

public enum ClassCost {
    /// What a class cost, readable at tiny sizes: 0 -> "free", $0.0006 -> "0.06¢", $0.034 -> "3.4¢", $1.20 -> "$1.20".
    public static func label(_ usd: Double) -> String {
        if usd <= 0 { return "free" }
        if usd < 0.01 { return String(format: "%.2f¢", usd * 100) }
        if usd < 1 { return String(format: "%.1f¢", usd * 100) }
        return String(format: "$%.2f", usd)
    }
}

public enum ClassTime {
    /// 754 s -> "12:34"
    public static func mmss(_ s: Double) -> String { String(format: "%02d:%02d", Int(s) / 60, Int(s) % 60) }
}

/// Who spoke, for a transcript line: only online classes know (the Mac's sound is the teacher, the mic is Isaac).
public enum ClassWho {
    public static func label(source: String, online: Bool) -> String? {
        online ? (source == "mac" ? "Teacher" : "Me") : nil
    }
}
