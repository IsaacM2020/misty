import SwiftUI

/// The one thread with Kit, in a centred column. Isaac's lines sit right in a soft block; hers are plain text on the
/// left, with a status line while she works and a Yes / No card when a step needs him.
struct ChatView: View {
    @Environment(AppState.self) private var state
    @State private var draft = ""
    @FocusState private var focused: Bool

    private static let starters: [(icon: String, title: String, prompt: String)] = [
        ("calendar", "My week", "What's on this week?"),
        ("doc.text", "English work", "Do my English work"),
        ("pin", "Remember a doc", "My English task is this doc"),
    ]
    private static let column: CGFloat = 700

    var body: some View {
        let vm = state.chat
        VStack(spacing: 0) {
            if vm.messages.isEmpty {
                Spacer()
                VStack(spacing: 0) {
                    Orb(mood: state.mood, size: 64).padding(.bottom, 18)
                    Text(Self.greeting()).font(Theme.ui(28, 700)).tracking(-0.6)
                    HStack(spacing: 8) {
                        ForEach(Self.starters, id: \.title) { s in
                            Button { draft = s.prompt; focused = true } label: {
                                Label(s.title, systemImage: s.icon)
                            }
                            .buttonStyle(ChipStyle()).focusEffectDisabled()
                        }
                    }
                    .padding(.top, 26)
                }
                .frame(maxWidth: .infinity)
                Spacer()
                Spacer()
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        let lastHer = vm.messages.last(where: { $0.role != "user" })?.id
                        LazyVStack(alignment: .leading, spacing: 22) {
                            ForEach(vm.messages) { m in
                                MessageRow(message: m, latest: m.id == lastHer)
                                    .transition(.asymmetric(insertion: .move(edge: .bottom).combined(with: .opacity), removal: .opacity))
                            }
                            Color.clear.frame(height: 1).id("end")
                        }
                        .frame(maxWidth: Self.column)
                        .padding(.horizontal, Page.gutter).padding(.top, 8).padding(.bottom, 18)
                        .frame(maxWidth: .infinity)
                    }
                    .scrollContentBackground(.hidden)
                    .animation(Motion.or(Motion.morph), value: vm.messages.count)
                    .onChange(of: vm.messages) { proxy.scrollTo("end", anchor: .bottom) }
                    .onAppear { proxy.scrollTo("end", anchor: .bottom) }
                }
            }
            Composer(draft: $draft, focused: $focused, busy: vm.busy) { send() }
                .frame(maxWidth: Self.column)
                .padding(.horizontal, Page.gutter).padding(.bottom, 18).padding(.top, 6)
        }
        .onAppear { focused = true }
    }

    /// "Morning, Isaac." by the clock on this Mac.
    static func greeting(at date: Date = Date()) -> String {
        switch Calendar.current.component(.hour, from: date) {
        case 5..<12: return "Morning, Isaac."
        case 12..<17: return "Afternoon, Isaac."
        case 17..<22: return "Evening, Isaac."
        default: return "Up late, Isaac?"
        }
    }

    private func send() {
        let text = draft
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !state.chat.busy else { return }
        draft = ""
        Task { await state.chat.send(text) }
    }
}

/// A starter suggestion: an outlined chip that fills a little under the pointer.
private struct ChipStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { ChipFace(label: configuration.label, pressed: configuration.isPressed) }
}
private struct ChipFace<L: View>: View {
    let label: L
    let pressed: Bool
    @State private var hovering = false
    var body: some View {
        label.labelStyle(StarterLabel()).font(Theme.ui(13, 550)).foregroundStyle(Theme.text)
            .padding(.leading, 8).padding(.trailing, 14).padding(.vertical, 8)
            .background(Capsule().fill(Theme.raised).shadow(color: Theme.shadow, radius: hovering ? 10 : 5, y: hovering ? 4 : 2))
            .overlay(Capsule().strokeBorder(Theme.line))
            .scaleEffect(pressed ? 0.95 : hovering ? 1.03 : 1)
            .contentShape(Capsule())
            .onHover { hovering = $0 }
            .animation(Motion.or(Motion.quick), value: pressed)
            .animation(Motion.or(Motion.morph), value: hovering)
    }
}

/// A starter's icon sits in a small iris dot so the row reads before the words do.
private struct StarterLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 8) {
            configuration.icon.font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.accent)
                .frame(width: 24, height: 24).background(Circle().fill(Theme.emberSoft))
            configuration.title
        }
    }
}

private struct MessageRow: View {
    @Environment(AppState.self) private var state
    let message: ChatMessage
    var latest = false
    @State private var open = false

    /// Older long replies fold to their opening so the thread stays glanceable; the latest one is always whole.
    private static let foldAt = 240

    var body: some View {
        if message.role == "user" {
            HStack {
                Spacer(minLength: 120)
                Text(message.text).font(Theme.ui(14, 450)).lineSpacing(3).textSelection(.enabled)
                    .padding(.horizontal, 15).padding(.vertical, 10)
                    .background(Theme.panel, in: BubbleShape())
                    .foregroundStyle(Theme.text)
            }
        } else {
            let text = Pretty.reply(message.text)
            let folds = !latest && !message.pending && text.count > Self.foldAt
            HStack(alignment: .top, spacing: 12) {
                Orb(mood: latest && message.pending ? .thinking : .idle, size: 20)
                    .opacity(latest ? 1 : 0.55)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 10) {
                    if !text.isEmpty {
                        Text(folds && !open ? Self.opening(text) : text)
                            .font(Theme.ui(14.5, 400)).lineSpacing(5).foregroundStyle(Theme.text).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                        if folds {
                            Button { withAnimation(Motion.or(Motion.morph)) { open.toggle() } } label: {
                                Image(systemName: open ? "chevron.up" : "ellipsis")
                                    .font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.dim)
                                    .frame(width: 34, height: 20).background(Capsule().fill(Theme.panel))
                            }
                            .buttonStyle(.plain).focusEffectDisabled().help(open ? "Fold" : "Show all")
                        }
                    }
                    if message.pending {
                        WorkingLine(text: message.status ?? (text.isEmpty ? "Thinking" : nil))
                    }
                    if let c = message.confirm { ConfirmRow(turn: message.id, confirm: c) }
                }
                .frame(maxWidth: 580, alignment: .leading)
            }
        }
    }

    /// The first paragraph, cut at a sentence end near `foldAt` if it runs long.
    static func opening(_ text: String) -> String {
        let first = text.components(separatedBy: "\n\n").first ?? text
        guard first.count > foldAt else { return first }
        let head = String(first.prefix(foldAt))
        if let r = head.range(of: ". ", options: .backwards), head.distance(from: head.startIndex, to: r.lowerBound) > 90 {
            return String(head[..<r.upperBound]) + "…"
        }
        return head + "…"
    }
}

/// His bubble: round, with the corner nearest the composer tucked in.
private struct BubbleShape: Shape {
    func path(in r: CGRect) -> Path {
        Path(roundedRect: r, cornerRadii: .init(topLeading: 18, bottomLeading: 18, bottomTrailing: 6, topTrailing: 18), style: .continuous)
    }
}

/// A small turning orb while Kit works, with what she is doing.
private struct WorkingLine: View {
    let text: String?
    var body: some View {
        if let text {
            HStack(spacing: 8) {
                Text(text.replacingOccurrences(of: "…", with: "")).font(Theme.ui(13, 500)).foregroundStyle(Theme.dim)
                PulseDots()
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Capsule().fill(Theme.panel))
        }
    }
}

/// Three dots that rise in turn: she's on it.
private struct PulseDots: View {
    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30)) { tl in
            let t = tl.date.timeIntervalSinceReferenceDate
            HStack(spacing: 3) {
                ForEach(0..<3, id: \.self) { i in
                    let k = max(0, sin(t * 6 - Double(i) * 0.9))
                    Circle().fill(Theme.ember).frame(width: 4.5, height: 4.5)
                        .offset(y: -3 * k).opacity(0.4 + 0.6 * k)
                }
            }
        }
        .frame(height: 10)
    }
}

private struct ConfirmRow: View {
    @Environment(AppState.self) private var state
    let turn: String
    let confirm: ChatConfirm

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(confirm.question).font(Theme.ui(14, 500)).fixedSize(horizontal: false, vertical: true)
            if let a = confirm.answer {
                Label(a ? "Yes" : "No", systemImage: a ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .font(Theme.ui(12.5, 600)).foregroundStyle(a ? Theme.good : Theme.dim)
            } else {
                HStack(spacing: 6) {
                    GoButton("Yes") { Task { await state.chat.answer(turn: turn, yes: true) } }
                    GoButton("No", filled: false) { Task { await state.chat.answer(turn: turn, yes: false) } }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.raised)
            .shadow(color: Theme.shadow, radius: 12, y: 5))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Theme.ember.opacity(0.35)))
    }
}

/// Where he types: a lifted box with a mic (talk instead) and a round send button that lights up when there is text.
private struct Composer: View {
    @Environment(AppState.self) private var state
    @Binding var draft: String
    var focused: FocusState<Bool>.Binding
    let busy: Bool
    let send: () -> Void

    var body: some View {
        let ready = !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !busy
        let talking = state.voice.state != .idle
        HStack(alignment: .bottom, spacing: 6) {
            TextField("Ask EDITH", text: $draft, axis: .vertical)
                .textFieldStyle(.plain).font(Theme.ui(14, 400))
                .lineLimit(1...8)
                .focused(focused)
                .onSubmit(send)
                .padding(.vertical, 6)
            IconButton(talking ? "waveform" : "mic", on: talking, help: talking ? "Stop" : "Talk to EDITH (Left Command+Option)") {
                if state.voice.state == .thinking || state.voice.state == .speaking { state.voice.end() }
                else { Task { await state.voice.tap() } }
            }
            Button(action: send) {
                Image(systemName: busy ? "ellipsis" : "arrow.up").font(.system(size: 12.5, weight: .bold))
                    .foregroundStyle(ready ? Color.white : Theme.faint)
                    .frame(width: 30, height: 30)
                    .background(Circle().fill(ready ? Theme.ember : Theme.panel))
                    .scaleEffect(ready ? 1 : 0.9)
            }
            .buttonStyle(.plain).focusEffectDisabled().disabled(!ready)
            .keyboardShortcut(.return, modifiers: .command)
            .animation(Motion.or(Motion.quick), value: ready)
        }
        .padding(.leading, 18).padding(.trailing, 9).padding(.vertical, 9)
        .background(Theme.raised, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(focused.wrappedValue ? Theme.ember.opacity(0.45) : Theme.line))
        .shadow(color: Theme.shadow, radius: focused.wrappedValue ? 18 : 12, y: focused.wrappedValue ? 7 : 4)
        .animation(Motion.or(Motion.morph), value: focused.wrappedValue)
    }
}

/// Memory tab, Facts: what Isaac told her, as a wall of small tiles, newest first. Each tile is an icon, the thing it
/// is about, and the fact in a few lines; Forget appears on hover.
struct FactsList: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let facts = state.chat.facts
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if facts.isEmpty {
                    VStack(spacing: 12) {
                        Orb(size: 52)
                        Text("Tell her things in Chat").font(Theme.ui(15, 600)).foregroundStyle(Theme.dim)
                    }
                    .frame(maxWidth: .infinity).padding(.top, 80)
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 230), spacing: 12)], alignment: .leading, spacing: 12) {
                        ForEach(facts) { FactTile(fact: $0) }
                    }
                    .animation(Motion.or(Motion.morph), value: facts.map(\.id))
                }
            }
            .frame(maxWidth: 900, alignment: .leading)
            .padding(.horizontal, Page.gutter)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 24)
        }
        .scrollContentBackground(.hidden)
        .task { await state.chat.loadFacts() }
    }
}

private struct FactTile: View {
    @Environment(AppState.self) private var state
    let fact: Fact
    @State private var hovering = false

    /// A guess at what kind of thing the fact is about, from its key.
    private var kind: (icon: String, plastic: Plastic) {
        let k = fact.key.lowercased()
        if fact.link != nil || k.contains("link") { return ("link", Theme.blue) }
        if ["teacher", "tutor", "friend", "mum", "dad", "coach"].contains(where: k.contains) { return ("person.fill", Theme.orange) }
        for s in ["physics", "chem", "math", "econ", "english", "spanish"] where k.contains(s) {
            return (SubjectStyle.icon(s), SubjectStyle.plastic(s))
        }
        if k.contains("essay") || k.contains("task") || k.contains("doc") { return ("doc.text.fill", Theme.yellow) }
        return ("pin.fill", Theme.limeSoft)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 9) {
                IconTile(icon: kind.icon, plastic: kind.plastic, side: 28)
                Text(fact.key.prefix(1).uppercased() + fact.key.dropFirst()).font(Theme.ui(13, 650)).lineLimit(1)
                Spacer(minLength: 4)
                Button { Task { await state.chat.forget(fact) } } label: {
                    Image(systemName: "xmark").font(.system(size: 9.5, weight: .bold)).foregroundStyle(Theme.dim)
                        .frame(width: 20, height: 20).background(Circle().fill(Theme.panel))
                }
                .buttonStyle(.plain).focusEffectDisabled().help("Forget").opacity(hovering ? 1 : 0)
            }
            Text(fact.text).font(Theme.ui(12.5, 400)).foregroundStyle(Theme.dim).lineSpacing(2)
                .lineLimit(hovering ? 8 : 3).fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if let l = fact.link {
                Button { open(l) } label: {
                    Label(fact.linkTitle ?? "Open", systemImage: "arrow.up.right").font(Theme.ui(11.5, 600))
                        .foregroundStyle(Theme.accent).lineLimit(1)
                        .padding(.horizontal, 8).padding(.vertical, 3).background(Capsule().fill(Theme.emberSoft))
                }
                .buttonStyle(.plain).help(l)
            }
        }
        .padding(13)
        .frame(maxWidth: .infinity, minHeight: 112, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.raised)
            .shadow(color: Theme.shadow, radius: hovering ? 14 : 8, y: hovering ? 6 : 3))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Theme.line))
        .scaleEffect(hovering ? 1.015 : 1)
        .animation(Motion.or(Motion.morph), value: hovering)
        .onHover { hovering = $0 }
    }

    private func open(_ l: String) {
        if l.hasPrefix("/") { NSWorkspace.shared.open(URL(fileURLWithPath: l)) }
        else if let u = URL(string: l) { NSWorkspace.shared.open(u) }
    }
}

/// The Memory tab: Facts (what Isaac told Kit) and the Graph (what Kit learnt from his files, mail and chats).
struct MemoryView: View {
    @Environment(AppState.self) private var state
    @State private var showGraph = false
    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Memory") {
                Segmented(options: [(false, "Facts"), (true, "Graph")], selection: $showGraph)
            }
            if showGraph { GraphScreen() } else { FactsList() }
        }
    }
}
