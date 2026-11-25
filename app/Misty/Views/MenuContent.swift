import SwiftUI

struct MenuContent: View {
    @Environment(AppState.self) private var state
    @Environment(\.openWindow) private var openWindow

    private func openMain(_ tab: String? = nil) {
        if let tab { state.wantTab = tab }
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }

    var body: some View {
        let v = state.voice.state
        let paused = state.status?.paused ?? false
        let n = state.suggestions.cards.count
        VStack(alignment: .leading, spacing: 12) {
            // Her, and what she is doing.
            HStack(spacing: 10) {
                Orb(mood: state.mood, size: 30)
                VStack(alignment: .leading, spacing: 1) {
                    Text("EDITH").font(Font.system(size: 15, weight: .heavy)).tracking(1.4)
                    HStack(spacing: 5) {
                        Circle().fill(state.strip.dot.color).frame(width: 6, height: 6)
                        Text(state.strip.text).lineLimit(1)
                    }
                    .font(Theme.ui(11.5, 500)).foregroundStyle(Theme.dim)
                    .help(state.strip.build ?? state.strip.text)
                    if state.voice.ambient != .off, v != .idle {
                        Text("Listening for your name").font(Theme.ui(11.5, 600)).foregroundStyle(Theme.lime)
                    }
                }
                Spacer()
                Text(state.strip.spend).font(Theme.ui(11.5, 500)).foregroundStyle(Theme.faint).monospacedDigit()
            }
            // The three things to do.
            HStack(spacing: 8) {
                BigKey(icon: v == .thinking || v == .speaking ? "stop.fill" : "waveform", title: v == .thinking || v == .speaking ? "Stop" : "Talk",
                       hint: "⌘⌥", lit: v != .idle) {
                    if v == .thinking || v == .speaking { state.voice.end() } else { Task { await state.voice.tap() } }
                }
                BigKey(icon: "tray.fill", title: "Inbox", badge: n > 0 ? n : nil) { openMain("Suggestions") }
                BigKey(icon: "graduationcap.fill", title: "Class", lit: state.classes.isOpen) {
                    ClassPanel.shared.show(state)
                }
            }
            // Quiet switches.
            HStack(spacing: 2) {
                IconButton(state.voice.speakReplies ? "speaker.wave.2.fill" : "speaker.slash.fill", on: !state.voice.speakReplies,
                           help: state.voice.speakReplies ? "Mute her voice" : "Unmute") {
                    state.voice.speakReplies.toggle()
                    if !state.voice.speakReplies, v == .speaking { state.voice.end() }
                }
                IconButton(paused ? "play.fill" : "pause.fill", on: paused, help: paused ? "Resume" : "Pause everything") {
                    Task { await state.setPaused(!paused) }
                }
                IconButton("ear", on: state.wake.settings.enabled && state.wake.listening,
                           help: state.wake.settings.enabled ? "Saying \u{201C}Edith\u{201D} wakes her: on. Tap to turn off" : "Saying \u{201C}Edith\u{201D} wakes her: off. Tap to turn on") {
                    state.setWakeEnabled(!state.wake.settings.enabled)
                }
                IconButton("macwindow", help: "Open EDITH (⌘O)") { openMain() }
                    .keyboardShortcut("o")
                IconButton("gearshape", help: "Settings") { openMain("Settings") }
                    .keyboardShortcut(",")
                IconButton("list.bullet.rectangle", help: "Live log: what she hears and decides (⌘L)") {
                    openWindow(id: "thoughts")
                    NSApp.activate(ignoringOtherApps: true)
                }
                .keyboardShortcut("l")
                Spacer()
                IconButton("power", help: "Quit EDITH") { state.stop(); NSApp.terminate(nil) }
                    .keyboardShortcut("q")
            }
        }
        .padding(14)
        .frame(width: 280)
        .background(Theme.bg)
        .foregroundStyle(Theme.text)
    }
}

/// A big square key in the popover: icon over a one-word label, an optional count badge.
private struct BigKey: View {
    let icon: String
    let title: String
    var hint: String? = nil
    var badge: Int? = nil
    var lit = false
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button { Sound.shared.play(.down); action() } label: {
            VStack(spacing: 6) {
                Image(systemName: icon).font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(lit ? Color.white : Theme.text)
                    .frame(width: 40, height: 40)
                    .background(Circle().fill(lit ? Theme.ember : Theme.panel))
                    .overlay(alignment: .topTrailing) {
                        if let badge {
                            Text("\(badge)").font(Theme.ui(10.5, 700)).monospacedDigit().foregroundStyle(.white)
                                .padding(.horizontal, 5).frame(minWidth: 18, minHeight: 18)
                                .background(Capsule().fill(Theme.ember)).offset(x: 6, y: -4)
                        }
                    }
                Text(title).font(Theme.ui(12, 600))
            }
            .frame(maxWidth: .infinity).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.raised)
                .shadow(color: Theme.shadow, radius: hovering ? 10 : 5, y: hovering ? 4 : 2))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Theme.line))
            .scaleEffect(hovering ? 1.03 : 1)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressDip())
        .focusEffectDisabled()
        .onHover { hovering = $0 }
        .animation(Motion.or(Motion.quick), value: hovering)
        .help(hint.map { "\(title) (\($0))" } ?? title)
    }
}

private struct PressDip: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.scaleEffect(configuration.isPressed ? 0.94 : 1)
            .animation(Motion.or(Motion.quick), value: configuration.isPressed)
    }
}

extension StripModel.Dot {
    var color: Color {
        switch self {
        case .green: return Theme.good
        case .amber: return Theme.warn
        case .red: return Theme.urgent
        }
    }
}
