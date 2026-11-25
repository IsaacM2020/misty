import Foundation

/// Any JSON value. Used for page frontmatter and for decoding RPC results generically.
public enum JSONValue: Codable, Equatable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let n): try c.encode(n)
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    public var stringValue: String? { if case .string(let s) = self { return s } else { return nil } }
    public var objectValue: [String: JSONValue]? { if case .object(let o) = self { return o } else { return nil } }
    public var intValue: Int? { if case .number(let n) = self { return Int(n) } else { return nil } }
    public subscript(key: String) -> JSONValue? { if case .object(let o) = self { return o[key] } else { return nil } }
}

struct AnyKey: CodingKey {
    var stringValue: String
    var intValue: Int?
    init(_ s: String) { stringValue = s }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { self.intValue = intValue; stringValue = String(intValue) }
}

public enum Wire {
    /// Dictionaries whose KEYS are data (queue names, frontmatter fields, source names), not Swift property names.
    static let rawContainers: Set<String> = [
        "queue", "fm", "by_source", "bySource", "health", "value", "enabled",
    ]

    static func snakeToCamel(_ s: String) -> String {
        guard s.contains("_") else { return s }
        let parts = s.split(separator: "_", omittingEmptySubsequences: true)
        guard let first = parts.first else { return s }
        return String(first) + parts.dropFirst().map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined()
    }

    static func camelToSnake(_ s: String) -> String {
        var out = ""
        for ch in s {
            if ch.isUppercase { out += "_" + ch.lowercased() } else { out.append(ch) }
        }
        return out
    }

    public static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .custom { path in
            let last = path.last!
            if path.dropLast().contains(where: { rawContainers.contains($0.stringValue) }) { return last }
            return AnyKey(snakeToCamel(last.stringValue))
        }
        return d
    }

    public static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .custom { path in
            let last = path.last!
            if path.dropLast().contains(where: { rawContainers.contains($0.stringValue) }) { return last }
            return AnyKey(camelToSnake(last.stringValue))
        }
        return e
    }

    /// For pass-through of already-wire-shaped JSON (no key conversion in either direction).
    public static func plainDecoder() -> JSONDecoder { JSONDecoder() }
    public static func plainEncoder() -> JSONEncoder { JSONEncoder() }
}

public struct Empty: Codable, Sendable { public init() {} }
