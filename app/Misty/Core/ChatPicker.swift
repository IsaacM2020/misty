import Foundation

/// Logic behind Settings > Chats (search, filter, select, what to save). Foundation only, so it is unit tested
/// without the UI. `selected` always means "read this chat": for WhatsApp those are the picked chats, for
/// iMessage everything except the excluded ones.
public struct ChatPickerModel: Equatable, Sendable {
    public var chats: [ChatInfoDTO]
    public var selected: Set<String>
    public var query = ""
    public var groupsOnly = false
    /// "source:chat_id" keys kept out of context packs (the lock toggle). Applied by the engine at once, not on Save.
    public private(set) var privateIds: Set<String> = []

    public init(chats: [ChatInfoDTO], selected: Set<String>) {
        self.chats = chats
        self.selected = selected
    }

    /// iMessage form: everything is read unless excluded.
    public init(chats: [ChatInfoDTO], excluded: Set<String>) {
        self.init(chats: chats, selected: Set(chats.map(\.id)).subtracting(excluded))
    }

    public static func isGroup(_ id: String) -> Bool {
        id.hasSuffix("@g.us") || id.contains(";+;")          // WhatsApp group jid, iMessage group guid
    }

    public var visible: [ChatInfoDTO] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return chats.filter { c in
            if groupsOnly && !Self.isGroup(c.id) { return false }
            return q.isEmpty || c.name.lowercased().contains(q)
        }
    }

    public mutating func toggle(_ id: String) {
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
    }

    public func isPrivate(_ id: String, source: String) -> Bool { privateIds.contains("\(source):\(id)") }
    public mutating func setPrivate(_ keys: Set<String>) { privateIds = keys }
    public mutating func togglePrivate(_ id: String, source: String) {
        let k = "\(source):\(id)"
        if privateIds.contains(k) { privateIds.remove(k) } else { privateIds.insert(k) }
    }

    public mutating func selectVisible() { selected.formUnion(visible.map(\.id)) }
    public mutating func clearVisible() { selected.subtract(visible.map(\.id)) }

    /// iMessage form: ids to exclude. Picks that vanished from the list stay in `selected`, never in `excluded`.
    public var excluded: Set<String> { Set(chats.map(\.id)).subtracting(selected) }

    public var summary: String {
        let base = "\(selected.count) of \(chats.count) chats selected"
        let shown = visible.count
        return shown == chats.count ? base : "\(base) (\(shown) shown)"
    }
}
