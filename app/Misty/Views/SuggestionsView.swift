import SwiftUI

/// The Inbox: what needs Isaac, grouped by when. Each row is one card. Kit has usually prepared it already, so the row
/// says what Go will do; pressing Go does it. Send, delete and pay never happen without him.
struct SuggestionsView: View {
    @Environment(AppState.self) private var state

    private static let groups: [(key: String, name: String)] = [
        ("now", "Now"), ("today", "Today"), ("this_week", "This week"), ("none", "Later")]

    var body: some View {
        let vm = state.suggestions
        VStack(alignment: .leading, spacing: 0) {
            PageHeader(title: "Inbox")
            if vm.cards.isEmpty {
                Spacer()
                VStack(spacing: 8) {
                    Orb(mood: state.mood, size: 64).padding(.bottom, 10)
                    Text("All clear").font(Theme.ui(20, 700))
                }
                .frame(maxWidth: .infinity)
                Spacer()
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(Self.groups, id: \.key) { g in
                            let rows = vm.cards.filter { $0.urgency == g.key || (g.key == "none" && !["now", "today", "this_week"].contains($0.urgency)) }
                            if !rows.isEmpty {
                                HStack(spacing: 7) {
                                    if g.key == "now" { Orb(size: 14) }
                                    Text(g.name).font(Theme.ui(13, 650)).foregroundStyle(g.key == "now" ? Theme.text : Theme.dim)
                                    Chip("\(rows.count)")
                                }
                                .padding(.leading, 4).padding(.bottom, 9)
                                Card {
                                    ForEach(Array(rows.enumerated()), id: \.element.id) { i, row in
                                        SuggestionRow(card: row, last: i == rows.count - 1, big: g.key == "now")
                                            .transition(.asymmetric(insertion: .opacity, removal: .move(edge: .trailing).combined(with: .opacity)))
                                    }
                                }
                                .padding(.bottom, 26)
                            }
                        }
                    }
                    .pageColumn().padding(.top, 2)
                    .animation(Motion.or(Motion.morph), value: vm.cards.map(\.id))
                }
                .scrollContentBackground(.hidden)
            }
        }
        .task { await vm.load() }
    }
}

private struct SuggestionRow: View {
    @Environment(AppState.self) private var state
    let card: Suggestion
    let last: Bool
    var big = false
    @State private var hovering = false

    /// Who it is from (name only). The icon already says which app.
    private var origin: String {
        if card.source == "gcal" { return "" }
        var who = (card.sender ?? "").trimmingCharacters(in: .whitespaces)
        if let lt = who.firstIndex(of: "<") { who = String(who[..<lt]) }
        who = who.trimmingCharacters(in: CharacterSet(charactersIn: " \""))
        if who.isEmpty, card.source != "gmail" { who = (card.title ?? "").components(separatedBy: " · ").first ?? "" }
        if let at = who.firstIndex(of: "@") {                  // a bare address: name the service, not the inbox
            let host = who[who.index(after: at)...].split(separator: ".")
            let label = host.count > 2 ? host[host.count - 3] : host.first ?? ""
            who = label.prefix(1).uppercased() + label.dropFirst()
        }
        if who.allSatisfy({ $0.isNumber || "+ -()".contains($0) }) { return "" }   // a bare phone number says nothing
        return who
    }

    /// One icon and colour per source, so a glance down the list says where things came from.
    private var source: (icon: String, plastic: Plastic) {
        switch card.source ?? "" {
        case "gmail": return ("envelope.fill", Theme.orange)
        case "gcal": return ("calendar", Theme.blue)
        case "whatsapp": return ("phone.bubble.fill", Theme.green)
        case "imessage": return ("message.fill", Theme.yellow)
        default: return ("sparkle", Theme.grey)
        }
    }

    var body: some View {
        let vm = state.suggestions
        let working = vm.progress[card.id]
        HStack(alignment: .center, spacing: 12) {
            IconTile(icon: source.icon, plastic: source.plastic, side: big ? 40 : 32)
            VStack(alignment: .leading, spacing: 4) {
                Text(card.text).font(Theme.ui(big ? 15.5 : 14, big ? 650 : 550)).foregroundStyle(Theme.text)
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    .help(card.text)
                StatusLine(card: card, working: working, origin: origin)
            }
            Spacer(minLength: 12)
            if working == nil {
                HStack(spacing: 0) {
                    Menu {
                        Button("In 1 hour") { Task { await vm.act(card, .snooze(.hour)) } }
                        Button("Tonight") { Task { await vm.act(card, .snooze(.tonight)) } }
                        Button("Tomorrow morning") { Task { await vm.act(card, .snooze(.tomorrow)) } }
                    } label: { Image(systemName: "clock") }
                        .menuStyle(.button).buttonStyle(.plain).menuIndicator(.hidden).fixedSize()
                        .foregroundStyle(Theme.dim).frame(width: 28, height: 28).help("Snooze")
                    Menu {
                        Button("Dismiss") { Task { await vm.act(card, .dismiss) } }
                        if vm.target(for: card) != .none { Button("Open source") { open(vm.target(for: card)) } }
                    } label: { Image(systemName: "ellipsis") }
                        .menuStyle(.button).buttonStyle(.plain).menuIndicator(.hidden).fixedSize()
                        .foregroundStyle(Theme.dim).frame(width: 28, height: 28).help("More")
                }
                .opacity(hovering ? 1 : 0)
            }
            action(vm: vm, working: working)
        }
        .padding(.horizontal, 12).padding(.vertical, big ? 15 : 11)
        .background(hovering ? Theme.hover : .clear)
        .overlay(alignment: .bottom) { if !last { Theme.line.frame(height: 1).padding(.leading, big ? 64 : 56) } }
        .contentShape(Rectangle())
        .onHover { h in withAnimation(.easeOut(duration: 0.1)) { hovering = h } }
    }

    @ViewBuilder private func action(vm: SuggestionsVM, working: String?) -> some View {
        if working != nil {
            ProgressView().controlSize(.small).frame(width: 60)
        } else {
            switch card.prepState {
            case "done", "none": tick(vm)
            case "failed": GoButton("Retry", filled: false) { Task { await vm.go(card) } }
            default:
                tick(vm)
                Button {
                    Sound.shared.play(.thunk)
                    Task { await vm.go(card) }
                } label: {
                    HStack(spacing: 5) {
                        if let k = card.plan?.kind { Image(systemName: StatusLine.icon(k)).font(.system(size: 10.5, weight: .bold)) }
                        Text("Go")
                    }
                }
                .buttonStyle(KeyStyle(plastic: Theme.black, size: 12.5, hPad: 13, vPad: 6))
                .focusEffectDisabled()
                .help(card.plan.map { $0.summary } ?? "Go")
            }
        }
    }

    /// Mark it done yourself.
    private func tick(_ vm: SuggestionsVM) -> some View {
        IconButton("checkmark.circle", help: "Mark done") { Sound.shared.play(.done); Task { await vm.act(card, .done) } }
    }

    private func open(_ t: SourceTarget) {
        switch t {
        case .file(let p): NSWorkspace.shared.open(URL(fileURLWithPath: p))
        case .url(let u): if let url = URL(string: u) { NSWorkspace.shared.open(url) }
        case .hint(let h):
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(h, forType: .string)
        case .none: break
        }
    }
}

/// The small second line: who it is from, and one glyph for where Go stands. The words live in the tooltip.
private struct StatusLine: View {
    let card: Suggestion
    let working: String?
    let origin: String

    var body: some View {
        HStack(spacing: 7) {
            if let b = badge {
                HStack(spacing: 4) {
                    Image(systemName: b.icon).font(.system(size: 9.5, weight: .bold))
                    if let w = b.word { Text(w).font(Theme.ui(11, 600)) }
                }
                .foregroundStyle(b.color)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(Capsule().fill(b.color.opacity(0.12)))
                .help(b.note)
            }
            if !origin.isEmpty {
                Text(origin).font(Theme.small).foregroundStyle(Theme.faint).lineLimit(1)
            }
        }
    }

    /// Glyph per plan kind.
    static func icon(_ kind: String) -> String {
        switch kind {
        case "calendar", "event": return "calendar.badge.plus"
        case "draft", "reply": return "square.and.pencil"
        case "reminder", "task": return "bell"
        case "stream": return "cursorarrow.rays"
        default: return "bolt.fill"
        }
    }

    private var badge: (icon: String, word: String?, color: Color, note: String)? {
        if let w = working { return ("ellipsis", w.replacingOccurrences(of: "…", with: ""), Theme.accent, w) }
        switch card.prepState {
        case "ready": return card.plan.map { (Self.icon($0.kind), "Ready", Theme.accent, $0.summary) }
        case "done": return ("checkmark", "Done", Theme.good, card.prepNote ?? "Done")
        case "none": return ("person.fill", nil, Theme.dim, card.prepNote ?? "Needs you")
        case "failed": return ("exclamationmark", "Failed", Theme.urgent, card.prepNote ?? "Couldn't do it")
        default: return nil
        }
    }
}
