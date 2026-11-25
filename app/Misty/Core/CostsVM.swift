import Foundation
import Observation

/// What the engine's `spend.breakdown` says: totals by job and day, then the newest replies with every model call inside.
public struct CostReport: Decodable, Equatable, Sendable {
    public struct Group: Decodable, Equatable, Sendable, Identifiable {
        public var label: String
        public var calls: Int
        public var usd: Double
        public var models: [String]
        public var id: String { label }
    }
    public struct Day: Decodable, Equatable, Sendable, Identifiable {
        public var day: String
        public var usd: Double
        public var id: String { day }
    }
    public struct Round: Decodable, Equatable, Sendable {
        public var `in`: Int
        public var cached: Int
        public var out: Int
        public var usd: Double
        public var tools: [String]?
    }
    public struct ToolSpend: Decodable, Equatable, Sendable {
        public var tool: String
        public var usd: Double
    }
    public struct Reply: Decodable, Equatable, Sendable, Identifiable {
        public var id: Int
        public var at: String
        public var question: String
        public var reply: String
        public var usd: Double
        public var rounds: [Round]
        public var tools: [String]
        public var toolUsd: [ToolSpend]
    }
    public var days: Int
    public var today: Double
    public var window: Double
    public var byPurpose: [Group]
    public var byDay: [Day]
    public var messages: [Reply]
}

public extension Double {
    /// Money the way a cost report wants it: tiny amounts keep their digits.
    var usdText: String { self >= 1 ? String(format: "$%.2f", self) : self >= 0.01 ? String(format: "$%.3f", self) : String(format: "$%.4f", self) }
}

@MainActor
@Observable
public final class CostsVM {
    public private(set) var report: CostReport?
    public private(set) var failed = false
    public var days = 7
    private let api: LiveAPI

    public init(api: LiveAPI) { self.api = api }

    public func load() async {
        struct P: Encodable, Sendable { var days: Int; var messages: Int }
        do {
            report = try await api.call("spend.breakdown", P(days: days, messages: 25))
            failed = false
        } catch { failed = true }
    }
}
