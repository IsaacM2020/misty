import Foundation

public enum RpcEvent: Equatable, Sendable {
    case statusChanged(Status)
    case suggestionNew(id: String, kind: String, urgency: String)
    case suggestionProgress(id: String, text: String)
    case buildProgress(BuildProgress)
    case handsRequest(reqId: String, op: String, args: JSONValue)
    case chatDelta(turn: String, text: String)
    case chatStatus(turn: String, text: String)
    case chatDone(turn: String)                                 // the model stream is over (the RPC reply follows later)
    case chatConfirm(turn: String, id: String, question: String)
    case classNotes(session: String, status: String)
    case slateShow(JSONValue)
    case slateAppend(JSONValue)
    case slateUpdate(block: Int, item: Int, text: String)
    case slateClose
    case slateHold
    case proactive(id: String, how: String, text: String)     // she spoke up on her own (the gate said it was a good moment)
    case unknown(String)

    /// Decode one notification line (`{"jsonrpc","method","params"}`).
    public static func decode(_ data: Data) throws -> RpcEvent {
        struct Envelope: Decodable { var method: String?; var params: JSONValue? }
        let env = try Wire.plainDecoder().decode(Envelope.self, from: data)
        guard let method = env.method else { return .unknown("") }
        let params = try Wire.plainEncoder().encode(env.params ?? .null)
        let dec = Wire.decoder()
        switch method {
        case "status.changed":
            return .statusChanged(try dec.decode(Status.self, from: params))
        case "suggestion.new":
            struct P: Decodable { var id: String; var kind: String; var urgency: String }
            let p = try dec.decode(P.self, from: params)
            return .suggestionNew(id: p.id, kind: p.kind, urgency: p.urgency)
        case "suggestions.progress":
            guard let p = env.params, let id = p["id"]?.stringValue else { return .unknown(method) }
            return .suggestionProgress(id: id, text: p["text"]?.stringValue ?? "")
        case "hands.request":
            guard let p = env.params, let id = p["req_id"]?.stringValue, let op = p["op"]?.stringValue else { return .unknown(method) }
            return .handsRequest(reqId: id, op: op, args: p["args"] ?? .object([:]))
        case "chat.delta", "chat.status":
            guard let p = env.params, let t = p["turn"]?.stringValue else { return .unknown(method) }
            let text = p["text"]?.stringValue ?? ""
            return method == "chat.delta" ? .chatDelta(turn: t, text: text) : .chatStatus(turn: t, text: text)
        case "chat.done":
            guard let t = env.params?["turn"]?.stringValue else { return .unknown(method) }
            return .chatDone(turn: t)
        case "proactive.say":
            guard let p = env.params, let id = p["id"]?.stringValue, let text = p["text"]?.stringValue else { return .unknown(method) }
            return .proactive(id: id, how: p["how"]?.stringValue ?? "say", text: text)
        case "chat.confirm":
            guard let p = env.params, let t = p["turn"]?.stringValue, let id = p["id"]?.stringValue else { return .unknown(method) }
            return .chatConfirm(turn: t, id: id, question: p["question"]?.stringValue ?? "")
        case "class.notes":
            guard let p = env.params, let s = p["session"]?.stringValue else { return .unknown(method) }
            return .classNotes(session: s, status: p["status"]?.stringValue ?? "")
        case "slate.show":
            guard let p = env.params else { return .unknown(method) }
            return .slateShow(p)
        case "slate.append":
            guard let p = env.params else { return .unknown(method) }
            return .slateAppend(p)
        case "slate.update":
            guard let p = env.params, let b = p["block"]?.intValue, let i = p["item"]?.intValue, let t = p["text"]?.stringValue else { return .unknown(method) }
            return .slateUpdate(block: b, item: i, text: t)
        case "slate.close": return .slateClose
        case "slate.hold": return .slateHold
        case "build.progress":
            if let b = try? dec.decode(BuildProgress.self, from: params) { return .buildProgress(b) }
            return .unknown(method)
        default:
            return .unknown(method)
        }
    }
}
