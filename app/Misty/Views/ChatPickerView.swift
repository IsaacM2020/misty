import SwiftUI

/// Settings > Choose chats. WhatsApp is opt-in (nothing is read until you tick a chat), iMessage is opt-out.
struct ChatPickerView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss

    private enum Tab: String, CaseIterable, Identifiable {
        case whatsapp = "WhatsApp", imessage = "iMessage"
        var id: String { rawValue }
    }

    @State private var tab: Tab = .whatsapp
    @State private var wa = ChatPickerModel(chats: [], selected: [])
    @State private var im = ChatPickerModel(chats: [], selected: [])
    @State private var loading = true
    @State private var saving = false
    @State private var error: String?
    @State private var listErrors: [Tab: String] = [:]

    private var model: Binding<ChatPickerModel> { tab == .whatsapp ? $wa : $im }
    private var sourceKey: String { tab == .whatsapp ? "whatsapp" : "imessage" }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Choose chats").font(Theme.title)
            Picker("", selection: $tab) {
                ForEach(Tab.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented).labelsHidden()

            Text(tab == .whatsapp
                 ? "Nothing from WhatsApp is read until you tick a chat. Pick the ones that matter to you."
                 : "iMessage is read by default. Untick a chat to leave it out.")
                .font(Theme.body).foregroundStyle(Theme.dim)

            HStack {
                TextField("Search chats", text: model.query)
                    .textFieldStyle(.roundedBorder)
                Toggle("Groups only", isOn: model.groupsOnly).toggleStyle(.checkbox)
            }
            HStack {
                Button("Select shown") { model.wrappedValue.selectVisible() }
                Button("Clear shown") { model.wrappedValue.clearVisible() }
                Spacer()
                Text(model.wrappedValue.summary).font(Theme.small).foregroundStyle(Theme.dim)
            }

            if loading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.wrappedValue.chats.isEmpty {
                Text(listErrors[tab] ?? "No chats found. EDITH needs Full Disk Access to list them.")
                    .foregroundStyle(Theme.dim).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(model.wrappedValue.visible) { c in
                    HStack {
                        Toggle(isOn: Binding(get: { model.wrappedValue.selected.contains(c.id) },
                                             set: { _ in model.wrappedValue.toggle(c.id) })) {
                            HStack(spacing: 8) {
                                Image(systemName: ChatPickerModel.isGroup(c.id) ? "person.2" : "person")
                                    .foregroundStyle(Theme.dim)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(c.name).lineLimit(1)
                                    Text("\(c.count) messages" + (c.lastMessageAt.map { " · last \($0.prefix(10))" } ?? ""))
                                        .font(Theme.small).foregroundStyle(Theme.dim)
                                }
                            }
                        }
                        Spacer()
                        let locked = model.wrappedValue.isPrivate(c.id, source: sourceKey)
                        Button { Task { await setPrivate(c.id, !locked) } } label: {
                            Image(systemName: locked ? "lock.fill" : "lock.open")
                                .foregroundStyle(locked ? Color.orange : Color.secondary)
                        }
                        .buttonStyle(.plain)
                        .help(locked ? "Kept out of context packs. Click to allow." : "Keep this chat out of context packs")
                    }
                }
            }

            Text("The lock keeps a chat out of context packs, summaries and answers. It is still searchable here, and it takes effect at once.")
                .font(Theme.small).foregroundStyle(Theme.dim)
            Text("Each picked chat is read from its start, one note per chat-week (about $0.001 each). The daily cap paces it, and Jev drops chit-chat before anything is written.")
                .font(Theme.small).foregroundStyle(Theme.dim)
            if let e = error { Text(e).font(Theme.body).foregroundStyle(Theme.warn) }
            HStack {
                Button("Cancel") { dismiss() }
                Spacer()
                Button("Save") { Task { await save() } }
                    .buttonStyle(.borderedProminent).disabled(loading || saving)
            }
        }
        .padding(22)
        .frame(width: 560, height: 680)
        .task { await load() }
    }

    private func load() async {
        defer { loading = false }
        let sel: ChatSelection? = try? await state.live.call("setup.chat_selection", Empty())
        let priv: PrivateChatList? = try? await state.live.call("chats.private_list", Empty())
        let keys = Set((priv?.private ?? []).map { "\($0.source):\($0.chatId)" })
        do {
            let l: ChatList = try await state.live.call("setup.list_whatsapp_chats", Empty())
            wa = ChatPickerModel(chats: l.chats.filter { $0.count > 0 }, selected: Set(sel?.whatsappPicked ?? []))
            wa.setPrivate(keys)
        } catch { listErrors[.whatsapp] = "Could not read the WhatsApp chat list (\(error))." }
        do {
            let l: ChatList = try await state.live.call("setup.list_imessage_chats", Empty())
            im = ChatPickerModel(chats: l.chats.filter { $0.count > 0 }, excluded: Set(sel?.imessageExcluded ?? []))
            im.setPrivate(keys)
        } catch { listErrors[.imessage] = "Could not read the iMessage chat list (\(error))." }
    }

    private func setPrivate(_ id: String, _ flag: Bool) async {
        struct P: Encodable, Sendable { var source: String; var chatId: String; var `private`: Bool }
        do {
            let _: JSONValue = try await state.live.call("chats.set_private", P(source: sourceKey, chatId: id, private: flag))
            if tab == .whatsapp { wa.togglePrivate(id, source: sourceKey) } else { im.togglePrivate(id, source: sourceKey) }
        } catch { self.error = "Could not change the lock: \(error)" }
    }

    private func save() async {
        saving = true; defer { saving = false }
        // a tab whose list failed to load (no Full Disk Access) is left out, never saved as "nothing selected"
        struct P: Encodable, Sendable {
            var enabled: [String: Bool]; var imessageExcluded: [String]?; var whatsappPicked: [String]?
        }
        var enabled: [String: Bool] = [:]
        if !wa.chats.isEmpty { enabled["whatsapp"] = !wa.selected.isEmpty }
        if !im.chats.isEmpty { enabled["imessage"] = !im.selected.isEmpty }
        do {
            let _: JSONValue = try await state.live.call("setup.save_sources", P(
                enabled: enabled,
                imessageExcluded: im.chats.isEmpty ? nil : Array(im.excluded).sorted(),
                whatsappPicked: wa.chats.isEmpty ? nil : Array(wa.selected).sorted()))
            dismiss()
        } catch { self.error = "Could not save: \(error)" }
    }
}
