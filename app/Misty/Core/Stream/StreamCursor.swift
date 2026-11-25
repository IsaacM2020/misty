import AppKit
import SwiftUI

/// Stream's own cursor: a see-through, click-through window over the screen with a teal pointer that glides along a
/// CursorPath to the control Stream is about to use and pulses when it presses. It is only a picture. The real mouse and
/// keyboard stay Isaac's, because presses go through Accessibility, not through the pointer.
@MainActor @Observable
final class StreamCursorModel {
    var position = CGPoint.zero          // window-local, y down: where the pointer's tip is
    var visible = false
    var opacity = 0.0
    var label = ""
    var thinking = false
    var moving = false
    var pressT = 1.0                     // 0 -> 1 over one press: drives the ring and the squish (1 = at rest)
}

@MainActor
public final class StreamCursor {
    public static let shared = StreamCursor()
    let model = StreamCursorModel()
    private var window: NSWindow?
    private var screen: CGRect = .zero   // Cocoa frame of the display the window covers
    private var glideTask: Task<Void, Never>?
    private var fadeTask: Task<Void, Never>?
    private var pressTask: Task<Void, Never>?

    /// One `cursor` hands op. Returns how many seconds the caller should wait (the glide) before acting.
    public func handle(_ what: String, frame: [Double]?, label: String) -> Double {
        switch what {
        case "aim":
            guard let f = frame, f.count == 4, f.allSatisfy(\.isFinite), f[2] > 0 || f[3] > 0 else { return 0 }
            return aim(at: CGRect(x: f[0], y: f[1], width: f[2], height: f[3]), label: label)
        case "click": press(); return 0.12
        case "think": think(label); return 0
        case "hide": fade(after: 0); return 0
        default: return 0
        }
    }

    /// A point in Accessibility coordinates (origin top-left of the main display, y down) inside a display whose Cocoa
    /// frame is `screen`, as a point in a window covering that display.
    nonisolated static func local(_ p: CGPoint, screen: CGRect, primaryHeight: CGFloat) -> CGPoint {
        CGPoint(x: p.x - screen.minX, y: p.y - (primaryHeight - screen.maxY))
    }

    private var primaryHeight: CGFloat { NSScreen.screens.first?.frame.height ?? 900 }

    private func display(for axPoint: CGPoint) -> CGRect {
        let cocoa = CGPoint(x: axPoint.x, y: primaryHeight - axPoint.y)
        return (NSScreen.screens.first { $0.frame.contains(cocoa) } ?? NSScreen.screens.first)?.frame
            ?? CGRect(x: 0, y: 0, width: 1440, height: 900)
    }

    private func ensureWindow(on frame: CGRect) -> NSWindow {
        let w = window ?? {
            let w = NSWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
            w.isOpaque = false
            w.backgroundColor = .clear
            w.hasShadow = false
            w.ignoresMouseEvents = true
            w.level = NSWindow.Level(rawValue: NSWindow.Level.popUpMenu.rawValue + 1)   // above open menus too
            w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: StreamCursorView(model: model))
            window = w
            return w
        }()
        if frame != screen {
            screen = frame
            w.setFrame(frame, display: true)
        }
        return w
    }

    /// Bring the cursor up on the display holding `axTarget`. A fresh cursor starts where Isaac's own pointer is, so it
    /// looks like it peels off from there.
    private func appear(near axTarget: CGPoint) {
        fadeTask?.cancel()
        let d = display(for: axTarget)
        let moved = d != screen
        let w = ensureWindow(on: d)
        if !model.visible || moved {
            let m = NSEvent.mouseLocation
            let start = d.contains(m) ? CGPoint(x: m.x, y: primaryHeight - m.y) : CGPoint(x: axTarget.x - 90, y: axTarget.y + 70)
            model.position = Self.local(start, screen: d, primaryHeight: primaryHeight)
            model.visible = true
        }
        w.orderFrontRegardless()
        withAnimation(.easeOut(duration: 0.18)) { model.opacity = 1 }
    }

    @discardableResult
    func aim(at rect: CGRect, label: String) -> Double {
        let target = CGPoint(x: rect.midX, y: rect.midY)
        appear(near: target)
        model.thinking = false
        model.label = label
        let to = Self.local(target, screen: screen, primaryHeight: primaryHeight)
        if hypot(to.x - model.position.x, to.y - model.position.y) < 4 { return 0 }
        glideTask?.cancel()
        let path = CursorPath(from: model.position, to: to)
        let duration = path.duration
        model.moving = true
        glideTask = Task { [weak self] in
            let start = Date()
            while !Task.isCancelled {
                let raw = min(1, Date().timeIntervalSince(start) / duration)
                let t = raw * raw * (3 - 2 * raw)                      // ease in and out
                guard let self else { return }
                self.model.position = path.point(at: t)
                if raw >= 1 { break }
                try? await Task.sleep(for: .milliseconds(8))
            }
            self?.model.moving = false
        }
        fade(after: 2.5)
        return duration
    }

    /// Driven frame by frame like the glide: SwiftUI's own keyframe animations did not tick in this overlay window.
    func press() {
        guard model.visible else { return }
        pressTask?.cancel()
        pressTask = Task { [weak self] in
            let start = Date()
            while !Task.isCancelled {
                let t = min(1, Date().timeIntervalSince(start) / 0.6)
                self?.model.pressT = t
                if t >= 1 { break }
                try? await Task.sleep(for: .milliseconds(8))
            }
        }
        fade(after: 2.0)
    }

    func think(_ label: String) {
        guard model.visible else { return }          // thinking alone never pops the cursor up from nowhere
        fadeTask?.cancel()
        model.thinking = true
        if !label.isEmpty { model.label = label }
        fade(after: 20)                              // a stuck run never leaves it on screen for good
    }

    private func fade(after secs: Double) {
        fadeTask?.cancel()
        fadeTask = Task { [weak self] in
            if secs > 0 { try? await Task.sleep(for: .seconds(secs)) }
            guard !Task.isCancelled, let self else { return }
            withAnimation(.easeIn(duration: 0.35)) { self.model.opacity = 0 }
            try? await Task.sleep(for: .milliseconds(380))
            guard !Task.isCancelled else { return }
            self.model.visible = false
            self.model.thinking = false
            self.window?.orderOut(nil)
        }
    }
}

/// The classic arrow, tip at (0, 0), about 20 pt tall.
private struct Arrow: Shape {
    func path(in _: CGRect) -> Path {
        var p = Path()
        let pts: [CGPoint] = [.init(x: 0, y: 0), .init(x: 0, y: 19), .init(x: 4.6, y: 14.6), .init(x: 7.8, y: 21.6),
                              .init(x: 10.8, y: 20.3), .init(x: 7.7, y: 13.5), .init(x: 13.8, y: 13.5)]
        p.addLines(pts)
        p.closeSubpath()
        return p
    }
}

struct StreamCursorView: View {
    let model: StreamCursorModel

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.clear
            if model.visible {
                ZStack(alignment: .topLeading) {
                    Pulse(t: model.pressT)
                    Arrow()
                        .fill(Theme.accent)
                        .overlay(Arrow().stroke(.white, style: StrokeStyle(lineWidth: 1.4, lineJoin: .round)))
                        .frame(width: 14, height: 22, alignment: .topLeading)
                        .shadow(color: .black.opacity(0.35), radius: 3, y: 1.5)
                        .scaleEffect(squish(model.pressT), anchor: .topLeading)
                        .scaleEffect(model.moving ? 1.08 : 1, anchor: .topLeading)
                        .animation(.easeOut(duration: 0.15), value: model.moving)
                    if !model.label.isEmpty || model.thinking {
                        Tag(text: model.thinking ? "Thinking" : model.label, pulsing: model.thinking)
                            .offset(x: 16, y: 21)
                    }
                }
                .offset(x: model.position.x, y: model.position.y)
                .opacity(model.opacity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .allowsHitTesting(false)
    }
}

/// The pointer dips to 80% in the first fifth of a press and springs back with a small overshoot.
private func squish(_ t: Double) -> Double {
    if t >= 1 { return 1 }
    if t < 0.2 { return 1 - 0.2 * (t / 0.2) }
    let u = (t - 0.2) / 0.8
    return 1 - 0.2 * (1 - u) * cos(u * .pi * 1.5)
}

/// The ring that spreads from the tip when Stream presses (eased out, fading as it grows).
private struct Pulse: View {
    let t: Double
    var body: some View {
        let e = 1 - pow(1 - t, 3)
        Circle()
            .stroke(Theme.accent, lineWidth: 2.6 - 1.4 * e)
            .background(Circle().fill(Theme.accent.opacity(0.22 * (1 - e))))
            .frame(width: 44, height: 44)
            .scaleEffect(0.1 + 0.9 * e)
            .opacity(t >= 1 ? 0 : 1 - e)
            .offset(x: -22, y: -22)
    }
}

/// The small dark label next to the pointer: what Stream is about to press.
private struct Tag: View {
    let text: String
    let pulsing: Bool
    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .lineLimit(1)
            .foregroundStyle(.white)
            .padding(.horizontal, 7).padding(.vertical, 3.5)
            .background(Color(white: 0.09).opacity(0.9), in: RoundedRectangle(cornerRadius: 5, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 5, style: .continuous).strokeBorder(.white.opacity(0.12), lineWidth: 0.5))
            .phaseAnimator(pulsing ? [1.0, 0.55] : [1.0]) { v, o in v.opacity(o) } animation: { _ in .easeInOut(duration: 0.7) }
            .fixedSize()
    }
}
