import Foundation
import Observation

public enum SuggestionAction: Equatable, Sendable {
    case done, dismiss
    case snooze(Until)
    public enum Until: String, Sendable { case hour = "1h", tonight, tomorrow }
}

public enum SourceTarget: Equatable, Sendable {
    case file(String)
    case url(String)
    case hint(String)
    case none
}

@MainActor
@Observable
public final class SuggestionsVM {
    public private(set) var cards: [Suggestion] = []
    public private(set) var lastError: String?
    /// Cards whose Go is running, with the latest progress line ("Planning…", "step 3: pressing New Note").
    public var progress: [String: String] = [:]
    private let api: EngineAPI
    private let account: Int

    public init(api: EngineAPI, gmailAccountIndex: Int = 0) {
        self.api = api
        self.account = gmailAccountIndex
    }

    public func load() async {
        do {
            let list: SuggestionList = try await api.call("suggestions.list", ["status": "new"])
            cards = list.suggestions.sorted { $0.createdAt > $1.createdAt }
        } catch { lastError = String(describing: error) }
    }

    /// A `suggestion.new` event arrived: refetch just that card so it carries title/locator, newest first.
    public func handleNew(_ s: Suggestion) {
        guard !cards.contains(where: { $0.id == s.id }) else { return }
        cards.insert(s, at: 0)
        cards.sort { $0.createdAt > $1.createdAt }
    }

    public func act(_ card: Suggestion, _ action: SuggestionAction) async {
        guard let idx = cards.firstIndex(where: { $0.id == card.id }) else { return }
        let removed = cards.remove(at: idx)                       // optimistic
        struct P: Encodable, Sendable { var id: String; var action: String; var until: String? }
        let p: P
        switch action {
        case .done: p = P(id: card.id, action: "done", until: nil)
        case .dismiss: p = P(id: card.id, action: "dismiss", until: nil)
        case .snooze(let u): p = P(id: card.id, action: "snooze", until: u.rawValue)
        }
        do {
            let _: JSONValue = try await api.call("suggestions.act", p)
        } catch {
            cards.insert(removed, at: min(idx, cards.count))      // roll back
            lastError = String(describing: error)
        }
    }

    /// Tap Go: the engine plans the card if it has no plan yet, then does it. The card stays until Isaac ticks it off,
    /// showing what was done (or why not).
    public func go(_ card: Suggestion) async {
        guard progress[card.id] == nil else { return }
        progress[card.id] = card.plan?.summary.isEmpty == false ? card.plan!.summary : "Working…"
        defer { progress[card.id] = nil }
        do {
            let r: GoResult = try await api.call("suggestions.go", ["id": card.id], timeout: .seconds(300))
            if let i = cards.firstIndex(where: { $0.id == card.id }) {
                cards[i].prepState = r.state
                cards[i].prepNote = r.note
            }
        } catch {
            if let i = cards.firstIndex(where: { $0.id == card.id }) {
                cards[i].prepState = "failed"
                cards[i].prepNote = "Couldn't reach the engine"
            }
            lastError = String(describing: error)
        }
    }

    public func target(for card: Suggestion) -> SourceTarget {
        guard let loc = card.locator else { return .none }
        if loc.hasPrefix("gmail:thread:") {
            let id = loc.dropFirst("gmail:thread:".count)
            return .url("https://mail.google.com/mail/u/\(account)/#all/\(id)")
        }
        if loc.hasPrefix("/") { return .file(loc) }
        if loc.hasPrefix("imessage:") || loc.hasPrefix("whatsapp:") { return .hint("Open Messages and search for this chat") }
        return .none
    }
}

@MainActor
@Observable
public final class PrioritiesVM {
    public private(set) var lines: [PriorityLine] = []
    public private(set) var fromClass: [ClassPriorityLine] = []
    public private(set) var status = "draft"
    public private(set) var diff: [DiffLine] = []
    public var accepted: Set<String> = []
    public private(set) var lastError: String?
    private let api: EngineAPI

    public init(api: EngineAPI) { self.api = api }

    public var hasChanges: Bool { !diff.isEmpty }

    public func load() async {
        do {
            let st: PrioritiesState = try await api.call("priorities.get", Empty())
            lines = st.lines
            fromClass = st.fromClass ?? []
            status = st.status
            let d: PrioritiesDiff = try await api.call("priorities.diff", Empty())
            diff = d.diff
            accepted = []
        } catch { lastError = String(describing: error) }
    }

    /// Tick a class item here and it's ticked in that class's notes too.
    public func setDone(_ id: String, _ done: Bool) async {
        struct P: Encodable, Sendable { var id: String; var done: Bool }
        do {
            let _: JSONValue = try await api.call("priorities.done", P(id: id, done: done))
            await load()
        } catch { lastError = String(describing: error) }
    }

    public func toggle(_ id: String) {
        if accepted.contains(id) { accepted.remove(id) } else { accepted.insert(id) }
    }
    public func acceptAll() { accepted = Set(diff.map(\.id)) }
    public func acceptNone() { accepted = [] }

    public func apply() async {
        struct P: Encodable, Sendable { var acceptedLineIds: [String] }
        do {
            let _: JSONValue = try await api.call("priorities.apply", P(acceptedLineIds: accepted.sorted()))
            await load()
        } catch { lastError = String(describing: error) }
    }
}
