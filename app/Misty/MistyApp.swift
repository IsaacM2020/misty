import SwiftUI

@main
struct MistyApp: App {
    @State private var state: AppState

    init() {
        let s = AppState()
        _state = State(initialValue: s)
        s.start()                              // the engine must run even when no window is open
        s.classes.onStarted = { ClassPanel.shared.show(s) }   // a class started by voice still opens the panel
        s.onSlate = { SlatePanel.shared.show(s) }
        struct SlateRows: Decodable, Sendable { var slates: [JSONValue] }
        struct SlateOne: Decodable, Sendable { var slate: JSONValue }
        struct SlateId: Encodable, Sendable { var id: Int }
        s.slate.history = { [live = s.live] in (try? await live.call("slate.list", Empty()) as SlateRows)?.slates ?? [] }
        s.slate.fetchSlate = { [live = s.live] id in (try? await live.call("slate.get", SlateId(id: id)) as SlateOne)?.slate }
        s.slate.onPill = { [weak s] word in Task { @MainActor in await s?.chat.send(word, voice: false) } }   // a read-back pill is the word he would say
        Self.demoSlateIfAsked(s)               // --demo-slate <slate.json>: plays a saved slate (design checks)
        s.classes.onAsk = { ClassAskPanel.shared.show(s) }    // "Physics starts in 2 min. Take notes?"
        Speaker.runSpeakTestIfAsked()          // --speak-test: says one line and logs ms to first audio
    }

    private static func demoSlateIfAsked(_ s: AppState) {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: "--demo-slate"), args.indices.contains(i + 1),
              let data = try? Data(contentsOf: URL(fileURLWithPath: args[i + 1])),
              let json = try? JSONDecoder().decode(JSONValue.self, from: data) else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { Task { @MainActor in await s.handle(.slateShow(json)) } }
    }

    var body: some Scene {
        MenuBarExtra {
            MenuContent().environment(state)
        } label: {
            MenuIcon().environment(state)
        }
        .menuBarExtraStyle(.window)

        Window("EDITH", id: "main") {
            RootView()
                .environment(state)
                .frame(minWidth: 760, minHeight: 520)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 980, height: 660)
        .defaultLaunchBehavior(.presented)

        Window("Live log", id: "thoughts") {
            ThoughtLogView().environment(state)
        }
        .defaultSize(width: 520, height: 680)
        .defaultLaunchBehavior(.suppressed)
    }
}

struct MenuIcon: View {
    @Environment(AppState.self) private var state
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        let v = state.voice.state
        let look: MenuMark.Look =
            (state.status?.paused ?? false) ? .paused
            : state.classes.isOpen ? .classLive
            : state.voice.ambient != .off && v != .idle ? .ambient
            : (v == .listening || v == .thinking || v == .speaking || v == .starting) ? .voice
            : state.status?.badge == true ? .waiting
            : !state.voice.speakReplies ? .muted
            : .idle
        Image(nsImage: MenuMark.image(look)).accessibilityLabel("EDITH, \(look.rawValue)")
            .onChange(of: state.openMainRequest) {              // V1: the voice pill was clicked
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }
    }
}

/// The menu bar mark: her ink drop as a template image, so it follows the menu bar's colour. Its shape is the state, so
/// a glance says what she's doing.
enum MenuMark {
    enum Look: String { case idle, waiting, voice, ambient = "listening for your name", classLive = "taking class notes", paused, muted }

    @MainActor static func image(_ look: Look) -> NSImage {
        let img = NSImage(size: NSSize(width: 18, height: 16), flipped: false) { _ in
            NSColor.black.withAlphaComponent(look == .paused ? 0.4 : 1).set()
            func drop(_ r: CGFloat, at p: NSPoint = NSPoint(x: 9, y: 8)) -> NSBezierPath {
                let path = NSBezierPath()
                let cg = InkDrop.path(in: CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r), t: 0.6, mood: .speaking).cgPath
                path.append(NSBezierPath(cgPath: cg))
                return path
            }
            switch look {
            case .idle, .paused: drop(6).fill()
            case .waiting:                                     // a small dot up to the right: something is waiting
                drop(5.2, at: NSPoint(x: 8, y: 7)).fill()
                NSBezierPath(ovalIn: NSRect(x: 13.4, y: 10.6, width: 4.2, height: 4.2)).fill()
            case .muted: let o = drop(5.2); o.lineWidth = 1.5; o.stroke()
            case .voice:                                       // a ring around a core: she is listening or talking
                let o = drop(6.8); o.lineWidth = 1.4; o.stroke()
                drop(3.2).fill()
            case .ambient:                                     // a ring with a small core: she is listening for her name only
                let o = drop(6.8); o.lineWidth = 1.4; o.stroke()
                drop(1.8).fill()
            case .classLive:                                   // a double ring: taking class notes
                let o = drop(6.8); o.lineWidth = 1.2; o.stroke()
                let i = drop(4); i.lineWidth = 1.2; i.stroke()
                drop(1.5).fill()
            }
            return true
        }
        img.isTemplate = true
        return img
    }
}

/// The Dock icon, drawn here (no icon file yet): a warm near-black tile with her ember orb glowing in the middle.
enum DockIcon {
    @MainActor static func image() -> NSImage {
        let size = NSSize(width: 512, height: 512)
        return NSImage(size: size, flipped: false) { rect in
            let tile = rect.insetBy(dx: 50, dy: 50)
            let body = NSBezierPath(roundedRect: tile, xRadius: 94, yRadius: 94)
            NSGradient(colors: [Theme.ns(0x2A2826), Theme.ns(0x141312)])?.draw(in: body, angle: -90)
            NSColor(white: 1, alpha: 0.10).setStroke()
            body.lineWidth = 2
            body.stroke()
            let c = NSPoint(x: tile.midX, y: tile.midY), r: CGFloat = 104
            NSGraphicsContext.saveGraphicsState()
            body.addClip()
            // glow, then the orb, then a soft highlight up to the left
            NSGradient(colors: [Theme.ns(0xFF6A2A, 0.55), Theme.ns(0xFF6A2A, 0)])?
                .draw(fromCenter: c, radius: r * 0.9, toCenter: c, radius: r * 2.1, options: [])
            let orb = NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
            orb.addClip()
            let hot = NSPoint(x: c.x - r * 0.32, y: c.y + r * 0.42)
            NSGradient(colors: [Theme.ns(0xFFD08A), Theme.ns(0xFF7A33), Theme.ns(0xDD3F18)], atLocations: [0, 0.5, 1], colorSpace: .sRGB)?
                .draw(fromCenter: hot, radius: 0, toCenter: c, radius: r * 1.25, options: [.drawsAfterEndingLocation])
            NSGradient(colors: [NSColor(white: 1, alpha: 0.6), NSColor(white: 1, alpha: 0)])?
                .draw(fromCenter: hot, radius: 0, toCenter: hot, radius: r * 0.5, options: [])
            NSGraphicsContext.restoreGraphicsState()
            return true
        }
    }
}
