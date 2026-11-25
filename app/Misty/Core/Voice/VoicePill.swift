import AppKit
import SwiftUI

/// A SwiftUI hosting view swallows the mouse-down before the window sees it, so `isMovableByWindowBackground` never
/// works on it. This one claims every click and hands it to the window to drag (1:1, from where it was grabbed).
@MainActor private protocol HoverSyncing { func syncHover() }

private final class DraggableHost<Content: View>: NSHostingView<Content>, HoverSyncing {
    /// V1: a click that did not move the pill (a drag runs inside `performDrag` and returns on mouse-up).
    var onClick: (() -> Void)?
    /// V6: the mouse is over the pill, and the wheel over it (a panel that is never key gets neither from SwiftUI).
    var onHover: ((Bool) -> Void)?
    var onScroll: ((CGFloat) -> Void)?
    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        for t in trackingAreas { removeTrackingArea(t) }
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self, userInfo: nil))
    }
    override func mouseEntered(with event: NSEvent) { report(true) }
    override func mouseExited(with event: NSEvent) { report(false) }
    /// V11 (18 Nov 2026, "it's stuck on my screen"): AppKit can miss the mouse-exit (the drag loop eats it, or the
    /// window shrinks out from under the pointer), and a pill that still thinks it is hovered never leaves. So the
    /// hover is checked against where the mouse really is after a drag and after every resize.
    private var hovered = false
    private func report(_ on: Bool) { guard on != hovered else { return }; hovered = on; onHover?(on) }
    func syncHover() {
        guard let w = window else { return }
        report(w.isVisible && w.frame.contains(NSEvent.mouseLocation))
    }
    override func scrollWheel(with event: NSEvent) { onScroll?(event.scrollingDeltaY * (event.hasPreciseScrollingDeltas ? 1 : 8)) }
    override func hitTest(_ point: NSPoint) -> NSView? { self }
    /// The pill never takes focus, so without this the first click is eaten as an activating click.
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    /// The drag is done here, not by `performDrag`: on current macOS the window server runs that drag itself and
    /// `performDrag` returns at once, before the window has moved, so every drag looked like a click and opened Chat.
    /// A press that moves more than `slop` points is a drag and never a click.
    ///
    /// V10 (17 Nov 2026, "a fast mouse makes it go out of hand"): the drag used to add up per-event deltas in
    /// `mouseDragged`. A pill that never becomes key loses drag events once the pointer outruns it, and the pill resizes
    /// (re-centring itself) while words arrive, so the sum drifted away from the hand. Now mouse-down runs its own
    /// tracking loop, which gets every drag event wherever the pointer is, and each step places the pill from where it
    /// was grabbed (pointer minus grab point, measured from the pill's top centre), so nothing can accumulate.
    private let slop: CGFloat = 4
    override func mouseDown(with event: NSEvent) {
        guard let w = window else { return }
        let press = NSEvent.mouseLocation
        let grab = NSPoint(x: press.x - w.frame.midX, y: press.y - w.frame.maxY)       // from the pill's top centre
        var dragging = false
        while let e = w.nextEvent(matching: [.leftMouseDragged, .leftMouseUp], until: .distantFuture, inMode: .eventTracking, dequeue: true) {
            if e.type == .leftMouseUp { break }
            let p = NSEvent.mouseLocation
            if !dragging, hypot(p.x - press.x, p.y - press.y) > slop { dragging = true }
            guard dragging else { continue }
            let f = w.frame
            w.setFrameOrigin(NSPoint(x: (p.x - grab.x - f.width / 2).rounded(), y: (p.y - grab.y - f.height).rounded()))
        }
        if !dragging { onClick?() }
        syncHover()
    }
}

private final class PillPanel: NSPanel {
    override var canBecomeKey: Bool { false }       // dragging the pill must not pull the cursor out of the text being dictated into
    override var canBecomeMain: Bool { false }
}

/// The small floating pill (live mode and dictation both use it): a window exactly as big as its contents, never takes
/// focus, can be dragged anywhere and remembers where it was dropped. No window shadow: a SwiftUI shadow is cut off at
/// the window edge and shows up as a grey box.
@MainActor
public final class PillWindow {
    private var window: NSPanel?
    private let content: () -> AnyView
    private let onClick: (() -> Void)?
    private let onHover: ((Bool) -> Void)?
    private let onScroll: ((CGFloat) -> Void)?
    private var fitGeneration = 0
    private var placing = false
    private static let spotKey = "misty.pillSpot"            // [x of the pill's centre, y of its top edge]

    public init(onClick: (() -> Void)? = nil, onHover: ((Bool) -> Void)? = nil, onScroll: ((CGFloat) -> Void)? = nil,
                _ content: @escaping () -> AnyView) {
        self.content = content; self.onClick = onClick; self.onHover = onHover; self.onScroll = onScroll
    }

    public func show() {
        let w = window ?? make()
        if !w.isVisible { place(w) }
        w.orderFrontRegardless()
        w.animator().alphaValue = 1
    }

    /// V11: whether the pointer is really over the pill right now (not what the last enter/exit event said).
    public var mouseInside: Bool {
        guard let w = window, w.isVisible, w.alphaValue > 0.01 else { return false }
        return w.frame.contains(NSEvent.mouseLocation)
    }

    /// Fade out. `then` runs after the fade and says whether to really remove it (live mode may have restarted).
    public func hide(then keep: @escaping @MainActor () -> Bool = { true }) {
        guard let w = window else { return }
        NSAnimationContext.runAnimationGroup({ $0.duration = 0.18; w.animator().alphaValue = 0 }) {
            MainActor.assumeIsolated { if keep() { w.orderOut(nil) } }
        }
    }

    private func make() -> NSPanel {
        let w = PillPanel(contentRect: NSRect(x: 0, y: 0, width: 300, height: 44),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        w.isOpaque = false
        w.backgroundColor = .clear
        w.hasShadow = false
        w.level = NSWindow.Level(rawValue: NSWindow.Level.popUpMenu.rawValue + 1)
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        w.isReleasedWhenClosed = false
        w.alphaValue = 0
        // SwiftUI's own window auto-sizing loops forever on a borderless panel, so it is off and the window is
        // sized here from the size the content reports.
        let host = DraggableHost(rootView: PillSizer(content: content()) { [weak self] size in self?.fit(to: size) })
        host.sizingOptions = []
        host.onClick = onClick
        host.onHover = onHover
        host.onScroll = onScroll
        w.contentView = host
        window = w
        NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: w, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.moved() }
        }
        return w
    }

    /// Resize to the content, keeping the pill's centre and top edge where they are. V6: it grows at once, and shrinks only
    /// after the card inside has finished its spring (the card is drawn inside the window, so a window that shrank first
    /// would cut it).
    private func fit(to size: CGSize) {
        guard let w = window, size.width > 1, size.height > 1,
              abs(w.frame.width - size.width) > 0.5 || abs(w.frame.height - size.height) > 0.5 else { return }
        fitGeneration += 1
        let gen = fitGeneration
        let shrinking = size.width < w.frame.width - 0.5 || size.height < w.frame.height - 0.5
        let apply = { [weak self] in
            MainActor.assumeIsolated {
                guard let self, gen == self.fitGeneration, let w = self.window else { return }
                self.placing = true; defer { self.placing = false }
                var f = NSRect(x: w.frame.midX - size.width / 2, y: w.frame.maxY - size.height, width: size.width, height: size.height)
                // V1: a tall pill never runs off the screen it is on.
                if let v = (w.screen ?? NSScreen.screens.first { $0.frame.intersects(w.frame) } ?? NSScreen.main)?.visibleFrame {
                    f = PillLayout.clamp(f, into: v)
                }
                w.setFrame(f, display: true)
                (w.contentView as? HoverSyncing)?.syncHover()
            }
        }
        if shrinking { DispatchQueue.main.asyncAfter(deadline: .now() + 0.5, execute: apply) } else { DispatchQueue.main.async(execute: apply) }
    }

    /// Where Isaac last dropped it, or top centre of the screen he is on, just under the menu bar.
    private func place(_ w: NSPanel) {
        guard !placing else { return }                       // moving a window can resize it, which lands back here
        placing = true; defer { placing = false }
        var origin: NSPoint?
        if let p = UserDefaults.standard.array(forKey: Self.spotKey) as? [Double], p.count == 2,
           NSScreen.screens.contains(where: { $0.frame.contains(NSPoint(x: p[0], y: p[1] - 4)) }) {
            origin = NSPoint(x: p[0] - w.frame.width / 2, y: p[1] - w.frame.height)
        } else {
            let mouse = NSEvent.mouseLocation
            let s = NSScreen.screens.first { $0.frame.contains(mouse) } ?? NSScreen.main
            if let v = s?.visibleFrame { origin = NSPoint(x: v.midX - w.frame.width / 2, y: v.maxY - w.frame.height - 8) }
        }
        if let o = origin, abs(o.x - w.frame.origin.x) > 0.5 || abs(o.y - w.frame.origin.y) > 0.5 { w.setFrameOrigin(o) }
    }

    private func moved() {
        guard !placing, let w = window else { return }
        UserDefaults.standard.set([Double(w.frame.midX), Double(w.frame.maxY)], forKey: Self.spotKey)
    }
}

/// The pill at the top of the screen while voice is on: a lime dot, what Misty is doing, and the words
/// (Isaac's as he says them, then the sentence she is speaking).
@MainActor
struct PillSizer: View {
    let content: AnyView
    let onSize: (CGSize) -> Void
    var body: some View {
        content.fixedSize().onGeometryChange(for: CGSize.self, of: { $0.size }) { onSize($0) }
    }
}

@MainActor
public final class VoicePill {
    private let pill: PillWindow
    private let vm: VoiceVM

    public init(vm: VoiceVM) {
        self.vm = vm
        pill = PillWindow(onClick: { [weak vm] in vm?.pillClicked() },
                          onHover: { [weak vm] in vm?.pillHover = $0 },
                          onScroll: { [weak vm] d in if let vm { vm.pillScroll = max(0, vm.pillScroll + d) } }) { AnyView(VoicePillView(vm: vm)) }
    }

    public func show() { pill.show() }
    public var mouseInside: Bool { pill.mouseInside }
    public func hide(keep: (@MainActor () -> Bool)? = nil) { pill.hide(then: keep ?? { self.vm.state == .idle && !self.vm.lingering }) }
}

/// The frosted material behind the card. It follows light and dark by itself (no appearance is forced).
struct Glass: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = .hudWindow
        v.blendingMode = .behindWindow
        v.state = .active
        return v
    }
    func updateNSView(_ v: NSVisualEffectView, context: Context) {}
}

/// V8: the glass card both pills are drawn in. The card springs (no overshoot) to its new size inside a window a hair bigger.
struct GlassCard<Content: View>: View {
    let card: CGSize
    var flat = false
    @ViewBuilder let content: () -> Content
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ZStack(alignment: .top) {
            content()
                .frame(width: card.width, height: card.height)
                .background { if flat { Rectangle().fill(scheme == .dark ? Color(white: 0.16, opacity: 0.92) : Color(white: 0.97, opacity: 0.92)) } else { Glass() } }
                .clipShape(RoundedRectangle(cornerRadius: PillLayout.cardCorner, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: PillLayout.cardCorner, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.12), lineWidth: 0.5))
                .animation(Motion.or(Motion.morph), value: card)
        }
        .frame(width: PillLayout.canvas(card).width, height: PillLayout.canvas(card).height, alignment: .top)
    }
}

/// V6, calmer in V8: one small light-glass card that morphs between states: a still dot while she waits for her name, the
/// real mic meter and his words rising in while he talks, three soft dots while she thinks, one line while she speaks (as
/// wide as her longest sentence so far, so it does not jump per sentence), and the whole reply while the mouse is over it.
struct VoicePillView: View {
    let vm: VoiceVM
    /// Test and screenshot hook: draw a flat fill instead of the live blur (a blur needs a real screen behind it).
    var flat = false
    @State private var reduceMotion = Motion.reduce

    private var screenWidth: CGFloat { (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame.width ?? 800 }

    /// The grey waiting dot sat on his screen all day. It is now off unless he turns it on (misty.showIdleDot): the pill only
    /// appears while she is hearing, thinking or speaking, and goes back to nothing (a 2 pt clear window) when she waits.
    @AppStorage("misty.showIdleDot") private var showIdleDot = false

    var body: some View {
        let mode = vm.pillMode
        if case .dot = mode, !showIdleDot, !flat {
            Color.clear.frame(width: 2, height: 2).allowsHitTesting(false)
        } else {
            let open = PillLayout.expandedText(mode: mode, reply: vm.reply, hovering: vm.pillHover)
            let card = PillLayout.cardSize(mode: mode, expanded: open, screenWidth: screenWidth, reply: vm.reply)
            GlassCard(card: card, flat: flat) { content(mode, open: open) }
                .animation(Motion.or(Motion.morph), value: mode.key)
                .animation(Motion.or(Motion.morph), value: open != nil)     // V11: hover open/close springs too, not pops
        }
    }

    @ViewBuilder
    private func content(_ mode: PillLayout.Mode, open: String?) -> some View {
        if let open {
            let tw = PillLayout.expandedTextWidth(screenWidth: screenWidth)
            let box = PillLayout.expandedTextHeight(open, screenWidth: screenWidth)
            HStack(alignment: .top, spacing: 8) {
                Circle().fill(Theme.lime).frame(width: 8, height: 8).padding(.top, 4)
                Text(open).font(.system(size: PillLayout.fontSize)).foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: tw, alignment: .leading)
                    .modifier(FollowBottom(box: box, scroll: vm.pillScroll))
                    .frame(width: tw, height: box, alignment: .topLeading).clipped()
            }
            .padding(.leading, 12).padding(.trailing, 14).padding(.vertical, 9)
            .transition(.arrive)
        } else {
            switch mode {
            case .dot:
                // her name was heard: the dot turns green (back to purple after 3 s or once the phrase is sent to her)
                Circle().fill(vm.nameHeard ? Color(nsColor: .systemGreen) : Theme.lime).frame(width: 8, height: 8)
                    .animation(Motion.or(Motion.quick), value: vm.nameHeard)
                    .transition(.arrive)
            case .thinking:
                ThreeDots(reduceMotion: reduceMotion).transition(.arrive)
            case .hearing(let t):
                HStack(spacing: 8) {
                    LevelMeter(history: vm.levels)
                    LiveWords(text: t)
                }.padding(.leading, 12).padding(.trailing, 14).transition(.arrive)
            case .speaking(let t), .done(let t), .note(let t):
                HStack(spacing: 8) {
                    Circle().fill(Theme.lime).frame(width: 8, height: 8)
                    line(t)
                }.padding(.leading, 12).padding(.trailing, 14).transition(.arrive)
            }
        }
    }

    /// One line, current words. A new sentence arrives softly; a long one is cut with an ellipsis (hover shows all of it).
    private func line(_ t: String) -> some View {
        Text(t).font(.system(size: PillLayout.fontSize)).foregroundStyle(.primary)
            .lineLimit(1).truncationMode(.tail)
            .frame(maxWidth: .infinity, alignment: .leading)
            .id(t)
            .transition(.arrive)
            .animation(Motion.or(Motion.morph), value: t)
    }
}

/// Past six lines the text box shows the newest words (offset 0) until he scrolls up.
struct FollowBottom: ViewModifier {
    let box: CGFloat
    let scroll: CGFloat
    @State private var content: CGFloat = 0
    func body(content c: Content) -> some View {
        c.onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { content = $0 }
            .offset(y: -(max(0, content - box) - PillLayout.scrollOffset(requested: scroll, contentHeight: content, boxHeight: box)))
    }
}
