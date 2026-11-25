import Foundation

public struct SpendInfo: Codable, Equatable, Sendable {
    public var today: Double
    public var capDaily: Double
    public var build: Double?
    public var capBuild: Double
}

public struct BuildProgress: Codable, Equatable, Sendable {
    public var id: String
    public var phase: String
    public var done: Int
    public var total: Int
    public var failed: Int
    public var spentUsd: Double
    public var pausedBy: String?
}

public struct SourceHealth: Codable, Equatable, Sendable {
    public var source: String
    public var health: String
    public var lastOkAt: Double?
}

/// One cap that is holding jobs back (engine `capped`): group is "daily", "build" or a purpose such as "judge".
public struct CapCause: Codable, Equatable, Sendable {
    public var group: String
    public var waiting: Int
    public var spent: Double
    public var cap: Double
}

public struct Status: Codable, Equatable, Sendable {
    public var online: Bool
    public var paused: Bool
    public var queue: [String: Int]
    public var capped: [CapCause]?
    public var spend: SpendInfo
    public var build: BuildProgress?
    public var sources: [SourceHealth]
    public var badge: Bool
    public var health: JSONValue?
}

public struct Suggestion: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var itemId: String?
    public var kind: String
    public var text: String
    public var urgency: String
    public var status: String
    public var snoozeUntil: Double?
    public var sender: String?
    public var createdAt: Double
    public var updatedAt: Double
    public var locator: String?
    public var title: String?
    public var source: String?
    public var prep: String? = nil          // the Go plan as JSON, made in the background
    public var prepState: String? = nil     // nil | ready | none | running | done | failed
    public var prepNote: String? = nil      // what Go did, or why it could not

    /// The plan's one-line summary ("Add Zoom with Shveta, Sat 31 Oct 1:30pm") and kind (calendar, draft, ...).
    public var plan: (kind: String, summary: String)? {
        guard let s = prep, let d = s.data(using: .utf8),
              let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let k = o["plan"] as? String else { return nil }
        return (k, o["summary"] as? String ?? "")
    }
}

public struct GoResult: Codable, Equatable, Sendable {
    public var state: String
    public var note: String
}

public struct SuggestionList: Codable, Equatable, Sendable {
    public var suggestions: [Suggestion]
}

public struct GraphNode: Codable, Equatable, Sendable {
    public var id: String
    public var label: String
    public var type: String
    public var area: String?
    public var importance: Int
    public var glow: Double
    public var lastDate: String?
    public var sources: Int? = nil           // public notes behind it
    public var more: Int? = nil              // area nodes: eligible entities not shown ("+N")
    public var expanded: Bool? = nil         // area nodes: showing its top 12
    public var members: Int? = nil           // group nodes: chat-only people folded in
}

public struct GraphEdge: Codable, Equatable, Sendable {
    public var source: String
    public var target: String
    public var kind: String                  // belongs | involves | related
    public var weight: Int? = nil            // shared notes, for involves and related
}

/// `graph.get` parameters: which areas are open, which entity to bring in, a name to search for.
public struct GraphParams: Encodable, Equatable, Sendable {
    public var expanded: [String]
    public var focus: String?
    public var q: String?
}

public struct PrivateChat: Codable, Equatable, Sendable {
    public var source: String
    public var chatId: String
}

public struct PrivateChatList: Codable, Equatable, Sendable {
    public var `private`: [PrivateChat]
}

public struct LookupCandidate: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var type: String
    public var confidence: Double
    public var why: String
}

public struct LookupResult: Codable, Equatable, Sendable {
    public var candidates: [LookupCandidate]
    public var ambiguous: Bool
}

public struct MergeRow: Codable, Equatable, Sendable, Identifiable {
    public var id: Int
    public var kept: String
    public var dropped: String
    public var keptName: String
    public var droppedName: String
    public var reason: String
    public var undone: Int
}

public struct MergeList: Codable, Equatable, Sendable {
    public var merges: [MergeRow]
}

public struct PackEntityDTO: Codable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var type: String
    public var path: String
    public var summary: String
    public var now: [String]
    public var freshnessDays: Int?
    public var why: String
}

public struct ContextPackDTO: Codable, Equatable, Sendable {
    public var text: String
    public var pages: [String]
    public var tokens: Int
    public var entities: [PackEntityDTO]
    public var openNow: [JSONValue]?
    public var dropped: [JSONValue]?
}

public struct GraphData: Codable, Equatable, Sendable {
    public var nodes: [GraphNode]
    public var edges: [GraphEdge]
}

public struct PageSource: Codable, Equatable, Sendable {
    public var itemId: String
    public var title: String
    public var locator: String
    public var source: String
}

public struct PageDetail: Codable, Equatable, Sendable {
    public var path: String
    public var fm: [String: JSONValue]
    public var body: String
    public var sources: [PageSource]
}

public struct PriorityLine: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var text: String
    public var area: String?
}

/// An action item from a class, on Priorities as a checkbox (done = the date it was ticked).
public struct ClassPriorityLine: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var text: String
    public var area: String?
    public var due: String?
    public var cls: String?
    public var done: String?
}

public struct PrioritiesState: Codable, Equatable, Sendable {
    public var status: String
    public var lines: [PriorityLine]
    public var fromClass: [ClassPriorityLine]?
}

public struct DiffLine: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var op: String          // add | remove
    public var text: String
}

public struct PrioritiesDiff: Codable, Equatable, Sendable {
    public var diff: [DiffLine]
}

public struct QueryHit: Codable, Equatable, Sendable {
    public var itemId: String
    public var title: String
    public var locator: String
    public var snippet: String
    public var score: Double
    public var source: String
}

public struct ContextPackInfo: Codable, Equatable, Sendable {
    public var text: String
    public var pages: [String]
    public var tokens: Int
}

public enum QueryResult: Equatable, Sendable {
    case results(route: String, hits: [QueryHit])
    case answer(route: String, text: String, citations: [String])
    case contextPack(route: String, pack: ContextPackInfo)
}

extension QueryResult: Decodable {
    private enum K: String, CodingKey { case route, results, answer, citations, contextPack }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        let route = try c.decode(String.self, forKey: .route)
        if let hits = try c.decodeIfPresent([QueryHit].self, forKey: .results) {
            self = .results(route: route, hits: hits)
        } else if let a = try c.decodeIfPresent(String.self, forKey: .answer) {
            self = .answer(route: route, text: a, citations: try c.decodeIfPresent([String].self, forKey: .citations) ?? [])
        } else {
            self = .contextPack(route: route, pack: try c.decode(ContextPackInfo.self, forKey: .contextPack))
        }
    }
}

public struct SourceEstimate: Codable, Equatable, Sendable {
    public var items: Int
    public var tokens: Int
}

public struct SetupEstimate: Codable, Equatable, Sendable {
    public var items: Int
    public var inTokens: Int
    public var outTokens: Int
    public var jevUsd: Double
    public var haikuUsd: Double
    public var totalUsd: Double
    public var bySource: [String: SourceEstimate]
    public var capUsd: Double
}

public struct OpenRouterTest: Codable, Equatable, Sendable {
    public var ok: Bool
    public var model: String
    public var zdr: Bool
    public var usd: Double
    public var jevOk: Bool
}

/// `setup.saved_state`: what earlier setup already stored (Wire maps the engine's snake_case keys to these).
public struct SavedState: Codable, Equatable, Sendable {
    public var openrouterKey: Bool
    public var googleEmail: String?
}

public struct ChatInfoDTO: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var lastMessageAt: String?
    public var count: Int
}

/// `setup.chat_selection`: what the Settings chat picker starts from.
public struct ChatSelection: Codable, Equatable, Sendable {
    public var imessageExcluded: [String]
    public var whatsappPicked: [String]
    public var enabled: [String: Bool]
}

public struct ChatList: Codable, Equatable, Sendable {
    public var chats: [ChatInfoDTO]
}
