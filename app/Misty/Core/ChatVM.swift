import Foundation
import Observation

public struct ChatConfirm: Equatable, Sendable {
    public var id: String
    public var question: String
    public var answer: Bool?
}

public struct ChatMessage: Identifiable, Equatable, Sendable {
    public var id: String
    public var role: String                 // user | assistant
    public var text: String
    public var status: String? = nil        // "Opening your doc…" while a tool runs
    public var confirm: ChatConfirm? = nil  // a risky step waiting for Isaac
    public var pending = false
}

public struct Fact: Identifiable, Equatable, Decodable, Sendable {
    public var id: String
    public var key: String
    public var text: String
    public var link: String?
    public var linkTitle: String?
    public var updatedAt: Double
}

/// The one chat thread. Misty's reply streams into its message as `chat.delta` events arrive.
@MainActor
@Observable
public final class ChatVM {
    public private(set) var messages: [ChatMessage] = []
    public private(set) var facts: [Fact] = []
    public private(set) var busy = false
    public private(set) var lastError: String?
    private let api: EngineAPI

    public init(api: EngineAPI) { self.api = api }

    /// Reads the frame of his last app (step C1). Set by the app at start; nil in tests. Typed messages only: voice joins in C3.
    public var frameProvider: (@Sendable () async -> JSONValue?)?

    public func load() async {
        struct Row: Decodable { var id: Int; var role: String; var text: String }
        struct H: Decodable { var messages: [Row] }
        do {
            let h: H = try await api.call("chat.history", ["limit": 80])
            if !busy { messages = h.messages.map { ChatMessage(id: "m\($0.id)", role: $0.role, text: $0.text) } }
        } catch { lastError = String(describing: error) }
    }

    /// `voice`: the reply is spoken, so the engine answers in short spoken sentences. `turn` lets the voice loop
    /// know the id before the reply streams.
    /// Returns the engine's full reply (nil when it never came). The deltas that stream in on the event channel and
    /// this answer travel separately, so the answer can land first: the whole reply wins over a shorter streamed one.
    @discardableResult
    public func send(_ raw: String, voice: Bool = false, turn given: String? = nil, frame heldFrame: JSONValue? = nil) async -> String? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return nil }
        busy = true
        defer { busy = false }
        let turn = given ?? "t" + UUID().uuidString.prefix(8).lowercased()
        messages.append(ChatMessage(id: "u" + turn, role: "user", text: text))
        messages.append(ChatMessage(id: turn, role: "assistant", text: "", pending: true))
        struct P: Encodable, Sendable { var text: String; var turn: String; var voice: Bool; var frame: JSONValue? }
        struct R: Decodable { var reply: String }
        var full: String?
        var sentFrame = heldFrame
        if sentFrame == nil { sentFrame = await frameProvider?() }
        do {
            let r: R = try await api.call("chat.send", P(text: text, turn: turn, voice: voice, frame: sentFrame), timeout: .seconds(600))
            full = r.reply
            update(turn) { m in
                if ReplyTail.missing(shown: m.text, reply: r.reply) != nil { m.text = r.reply }
            }
        } catch {
            update(turn) { m in if m.text.isEmpty { m.text = "Couldn't reach EDITH's engine." } }
        }
        update(turn) { m in m.pending = false; m.status = nil }
        await loadFacts()
        return full
    }

    public func delta(turn: String, text: String) { update(turn) { $0.text += text } }
    public func status(turn: String, text: String) { update(turn) { $0.status = text } }
    public func ask(turn: String, id: String, question: String) {
        update(turn) { $0.confirm = ChatConfirm(id: id, question: question) }
    }

    public func answer(turn: String, yes: Bool) async {
        guard let m = messages.first(where: { $0.id == turn }), let c = m.confirm, c.answer == nil else { return }
        update(turn) { $0.confirm?.answer = yes }
        struct P: Encodable, Sendable { var id: String; var yes: Bool }
        let _: JSONValue? = try? await api.call("chat.answer", P(id: c.id, yes: yes))
    }

    public func loadFacts() async {
        struct L: Decodable { var facts: [Fact] }
        if let l: L = try? await api.call("facts.list", [String: String]()) { facts = l.facts }
    }

    public func forget(_ f: Fact) async {
        facts.removeAll { $0.id == f.id }
        let _: JSONValue? = try? await api.call("facts.forget", ["id": f.id])
    }

    private func update(_ id: String, _ change: (inout ChatMessage) -> Void) {
        guard let i = messages.firstIndex(where: { $0.id == id }) else { return }
        change(&messages[i])
    }
}


/// The streamed pieces of a reply and the engine's final answer reach the app by different routes, so the answer
/// can arrive before the last piece. This finds what is still missing.
enum ReplyTail {
    /// The part of `reply` that `shown` doesn't have yet, "" when the whole reply is there, nil when `shown` is not
    /// the start of `reply` (the reply was edited or replaced: don't splice).
    static func missing(shown: String, reply: String) -> String? {
        let a = shown.trimmingCharacters(in: .whitespacesAndNewlines)
        let b = reply.trimmingCharacters(in: .whitespacesAndNewlines)
        if a.isEmpty { return b.isEmpty ? nil : b }
        guard b.count > a.count, b.hasPrefix(a) else { return nil }
        return String(b.dropFirst(a.count))
    }
}
