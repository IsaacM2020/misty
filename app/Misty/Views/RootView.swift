import SwiftUI

enum Tab: String, CaseIterable, Identifiable {
    case chat = "Chat", suggestions = "Suggestions", priorities = "Priorities", classes = "Classes", graph = "Memory",
         radar = "Radar", log = "Live log", settings = "Settings"
    var id: String { rawValue }
    /// What the rail calls it. (The raw value stays, other code opens tabs by it.)
    var title: String { self == .suggestions ? "Inbox" : rawValue }
    var icon: String {
        switch self {
        case .chat: return "bubble.left.and.text.bubble.right"
        case .suggestions: return "tray"
        case .priorities: return "flag"
        case .classes: return "graduationcap"
        case .graph: return "brain"
        case .radar: return "dot.radiowaves.left.and.right"
        case .log: return "list.bullet.rectangle"
        case .settings: return "gearshape"
        }
    }
    var key: KeyEquivalent {
        switch self {
        case .chat: return "1"
        case .suggestions: return "2"
        case .priorities: return "3"
        case .classes: return "5"
        case .graph: return "4"
        case .radar: return "6"
        case .log: return "7"
        case .settings: return ","
        }
    }
}

struct RootView: View {
    @Environment(AppState.self) private var state
    @State private var tab: Tab = .chat

    var body: some View {
        Group {
            if !state.setupComplete {
                SetupView().background(Theme.bg)
            } else {
                HStack(spacing: 0) {
                    Sidebar(tab: $tab)
                    VStack(spacing: 0) {
                        BannerList()
                        ZStack {
                            switch tab {
                            case .chat: ChatView()
                            case .suggestions: SuggestionsView()
                            case .priorities: PrioritiesView()
                            case .classes: ClassesView()
                            case .graph: MemoryView()
                            case .radar: RadarView()
                            case .log: ThoughtLogView()
                            case .settings: SettingsView()
                            }
                        }
                        .id(tab)
                        .transition(.opacity)
                        .animation(.easeOut(duration: 0.14), value: tab)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    .padding(.top, 26)
                    .background(Theme.bg)
                }
            }
        }
        .font(Theme.body)
        .foregroundStyle(Theme.text)
        .tint(Theme.ember)
        .ignoresSafeArea()
        .onChange(of: state.wantTab, initial: true) { _, want in
            guard let want, let t = Tab(rawValue: want) else { return }
            tab = t
            state.wantTab = nil
        }
    }
}

extension AppState {
    /// What the orb should be doing right now.
    var mood: Orb.Mood {
        if status?.paused ?? false { return .off }
        switch voice.state {
        case .starting, .listening: return .listening
        case .thinking: return .thinking
        case .speaking: return .speaking
        case .idle: return chat.busy ? .thinking : .idle
        }
    }
}

/// The Mac's own frosted sidebar behind the rail.
private struct Frost: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = .sidebar
        v.blendingMode = .behindWindow
        v.state = .followsWindowActiveState
        return v
    }
    func updateNSView(_ v: NSVisualEffectView, context: Context) {}
}

/// Left rail: her (the orb and her name), the screens in two groups, and a small status block at the bottom.
private struct Sidebar: View {
    @Environment(AppState.self) private var state
    @Binding var tab: Tab

    private static let daily: [Tab] = [.chat, .suggestions, .priorities]
    private static let knows: [Tab] = [.classes, .graph, .radar, .log]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 9) {
                Orb(mood: state.mood, size: 24)
                Text("EDITH").font(Theme.wordmark).tracking(1.6)
            }
            .padding(.leading, 18).padding(.top, 52).padding(.bottom, 22)
            VStack(spacing: 1) { ForEach(Self.daily) { row($0) } }.padding(.horizontal, 10)
            Theme.line.frame(height: 1).padding(.horizontal, 20).padding(.vertical, 12)
            VStack(spacing: 1) { ForEach(Self.knows) { row($0) } }.padding(.horizontal, 10)
            Spacer(minLength: 12)
            row(.settings).padding(.horizontal, 10)
            Theme.line.frame(height: 1).padding(.horizontal, 18).padding(.top, 10)
            EngineFoot().padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 16)
        }
        .frame(width: 212)
        .frame(maxHeight: .infinity)
        .background(Theme.desk)
        .overlay(alignment: .trailing) { Theme.line.frame(width: 1) }
    }

    private func row(_ t: Tab) -> some View {
        let selected = tab == t
        let count = t == .suggestions && !state.suggestions.cards.isEmpty ? state.suggestions.cards.count : nil
        return Button { tab = t } label: {
            HStack(spacing: 9) {
                Image(systemName: t.icon).font(.system(size: 13, weight: selected ? .semibold : .medium))
                    .symbolVariant(selected ? .fill : .none)
                    .foregroundStyle(selected ? Theme.accent : Theme.dim).frame(width: 20)
                    .scaleEffect(selected ? 1.08 : 1)
                Text(t.title).font(Theme.ui(13.5, 500)).lineLimit(1).fixedSize()
                Spacer(minLength: 0)
                if let c = count {
                    Text("\(c)").font(Theme.ui(11, 700)).monospacedDigit().lineLimit(1).fixedSize()
                        .foregroundStyle(selected ? Color.white : Theme.accent)
                        .padding(.horizontal, 6).padding(.vertical, 1.5)
                        .background(Capsule().fill(selected ? Theme.ember : Theme.emberSoft))
                }
            }
            .foregroundStyle(selected ? Theme.text : Theme.dim)
            .padding(.horizontal, 9).padding(.vertical, 6.5)
            .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(RailStyle(selected: selected))
        .keyboardShortcut(t.key, modifiers: .command)
        .focusEffectDisabled()
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// A rail row: a soft block behind the current screen, a lighter one under the pointer.
private struct RailStyle: ButtonStyle {
    let selected: Bool
    func makeBody(configuration: Configuration) -> some View {
        RailFace(label: configuration.label, selected: selected, pressed: configuration.isPressed)
    }
}
private struct RailFace<L: View>: View {
    let label: L
    let selected: Bool
    let pressed: Bool
    @State private var hovering = false
    var body: some View {
        label
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(selected ? Theme.raised : Theme.ink.opacity(pressed ? 0.07 : hovering ? 0.04 : 0))
                .shadow(color: Theme.shadow.opacity(selected ? 1 : 0), radius: 6, y: 2))
            .scaleEffect(pressed ? 0.97 : 1)
            .animation(Motion.or(Motion.quick), value: pressed)
            .animation(Motion.or(Motion.morph), value: selected)
            .onHover { hovering = $0 }
    }
}

/// Engine dot, what it is doing, today's spend: the status block at the foot of the rail.
private struct EngineFoot: View {
    @Environment(AppState.self) private var state
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Circle().fill(state.strip.dot.color).frame(width: 6, height: 6).offset(y: -1)
                Text(state.strip.text).lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
            .font(Theme.ui(12, 500)).foregroundStyle(Theme.dim)
            Text(state.strip.spend).monospacedDigit()
                .font(Theme.ui(11.5, 400)).foregroundStyle(Theme.faint)
                .padding(.leading, 13)
                .help(state.strip.build ?? "Spent today / daily cap")
            if let f = state.strip.banners.first(where: { $0.id == "failed" }) {
                Button { Task { await state.perform(f.action) } } label: {
                    Label("Retry", systemImage: "arrow.clockwise").font(Theme.ui(11.5, 600)).foregroundStyle(Theme.warn)
                }
                .buttonStyle(.plain).padding(.leading, 13)
                .help(f.text)
            }
        }
    }
}

struct BannerList: View {
    @Environment(AppState.self) private var state

    /// What the Google banner says while sign-in is open, or why it didn't work.
    private func note(for b: Banner) -> String? { b.action == .reconnectGoogle ? state.googleNote : nil }

    /// A glyph for what the banner is about.
    static func icon(_ a: Banner.Action) -> String {
        switch a {
        case .raiseCap: return "gauge.with.dots.needle.100percent"
        case .reconnectGoogle: return "person.crop.circle.badge.exclamationmark"
        case .openFullDiskAccess: return "lock"
        case .exportWhatsApp: return "bubble.left.and.bubble.right"
        case .retryFailed: return "arrow.clockwise"
        case .openLogs: return "doc.text.magnifyingglass"
        case .none: return "exclamationmark"
        }
    }

    /// The banner's first clause only; the whole line sits in the tooltip.
    static func short(_ t: String) -> String {
        var cut = t
        for sep in [". ", "; ", " — "] { if let r = cut.range(of: sep) { cut = String(cut[..<r.lowerBound]) } }
        return cut.trimmingCharacters(in: CharacterSet(charactersIn: " ."))
    }

    var body: some View {
        VStack(spacing: 0) {
            ForEach(state.strip.banners.filter { $0.id != "failed" }) { b in   // failed jobs sit quietly in the rail foot
                let tone = b.severity == .error ? Theme.urgent : Theme.warn
                HStack(spacing: 9) {
                    Image(systemName: Self.icon(b.action)).font(.system(size: 11, weight: .bold)).foregroundStyle(tone)
                        .frame(width: 22, height: 22).background(Circle().fill(tone.opacity(0.14)))
                    Text(Self.short(note(for: b) ?? b.text)).font(Theme.ui(12.5, 550)).lineLimit(1)
                        .help(note(for: b) ?? b.text)
                    if let title = b.actionTitle {
                        GoButton(title, filled: false) { Task { await state.perform(b.action) } }
                            .disabled(b.action == .reconnectGoogle && state.googleNote == "Waiting for Google…")
                    }
                }
                .padding(.leading, 6).padding(.trailing, 5).padding(.vertical, 5)
                .background(Capsule().fill(Theme.raised).shadow(color: Theme.shadow, radius: 8, y: 3))
                .overlay(Capsule().strokeBorder(tone.opacity(0.25)))
                .transition(.move(edge: .top).combined(with: .opacity))
                .padding(.bottom, 8)
            }
        }
    }
}
