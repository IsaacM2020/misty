import SwiftUI

// The parts every screen is built from: buttons, the page header, section labels, cards, tags and the orb.

/// A filled button: a rounded tablet that dips a little when pressed. `held` rests it pressed in (used for toggles).
struct KeyStyle: ButtonStyle {
    var plastic: Plastic = Theme.light
    var size: CGFloat = 12.5
    var hPad: CGFloat = 12
    var vPad: CGFloat = 6
    var held = false
    var silent = false

    func makeBody(configuration: Configuration) -> some View {
        KeyFace(label: configuration.label, plastic: plastic, size: size, hPad: hPad, vPad: vPad,
                down: configuration.isPressed || held, interactive: true)
    }
}

/// A glyph or number in a soft rounded square. Not a button.
struct KeyCap: View {
    let text: String
    var plastic: Plastic = Theme.light
    var size: CGFloat = 15
    var side: CGFloat = 34
    var body: some View {
        Text(text)
            .font(Theme.ui(size, 700)).monospacedDigit()
            .foregroundStyle(plastic.fg)
            .frame(width: side, height: side)
            .background(RoundedRectangle(cornerRadius: side * 0.28, style: .continuous).fill(plastic.fill))
    }
}

/// An SF Symbol in a soft rounded square: where a thing came from, or what a row is.
struct IconTile: View {
    let icon: String
    var plastic: Plastic = Theme.light
    var side: CGFloat = 32
    var body: some View {
        Image(systemName: icon)
            .font(.system(size: side * 0.44, weight: .semibold))
            .foregroundStyle(plastic.fg)
            .frame(width: side, height: side)
            .background(RoundedRectangle(cornerRadius: side * 0.28, style: .continuous).fill(plastic.fill))
    }
}

struct KeyFace<L: View>: View {
    let label: L
    let plastic: Plastic
    let size: CGFloat
    let hPad: CGFloat
    let vPad: CGFloat
    let down: Bool
    let interactive: Bool
    @State private var hovering = false
    @Environment(\.isEnabled) private var enabled

    var body: some View {
        label
            .font(Theme.ui(size, 600))
            .foregroundStyle(plastic.fg)
            .padding(.horizontal, hPad).padding(.vertical, vPad)
            .background {
                Capsule(style: .continuous).fill(plastic.fill)
                    .overlay { Capsule(style: .continuous).fill(Theme.ink.opacity(interactive && hovering && !down ? 0.06 : 0)) }
            }
            .scaleEffect(down ? 0.93 : (interactive && hovering ? 1.03 : 1))
            .opacity(enabled ? 1 : 0.4)
            .animation(Motion.or(Motion.quick), value: down)
            .animation(Motion.or(Motion.morph), value: hovering)
            .contentShape(Capsule(style: .continuous))
            .onHover { if interactive { hovering = $0 } }
    }
}

/// A flat text button: dim until you point at it.
struct QuietStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        QuietFace(label: configuration.label, pressed: configuration.isPressed)
    }
}

private struct QuietFace<L: View>: View {
    let label: L
    let pressed: Bool
    @State private var hovering = false
    var body: some View {
        label
            .font(Theme.ui(12.5, 500))
            .foregroundStyle(pressed || hovering ? Theme.text : Theme.dim)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(pressed ? Theme.line : hovering ? Theme.hover : .clear))
            .contentShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
            .onHover { hovering = $0 }
            .animation(.easeOut(duration: 0.12), value: hovering)
    }
}

/// The commit button. Filled is ink (the one main thing on a row or screen); unfilled is a quiet grey tablet.
struct GoButton: View {
    let title: String
    var filled = true
    let action: () -> Void
    init(_ title: String, filled: Bool = true, action: @escaping () -> Void) { self.title = title; self.filled = filled; self.action = action }

    var body: some View {
        Button {
            if filled { Sound.shared.play(.thunk) }
            action()
        } label: {
            Text(title).frame(minWidth: 22)
        }
        .buttonStyle(KeyStyle(plastic: filled ? Theme.black : Theme.light, size: 12.5, hPad: filled ? 14 : 11, vPad: 6))
        .focusEffectDisabled()
    }
}

struct QuietButton: View {
    let title: String
    let action: () -> Void
    init(_ title: String, action: @escaping () -> Void) { self.title = title; self.action = action }
    var body: some View {
        Button(action: action) { Text(title) }.buttonStyle(QuietStyle()).focusEffectDisabled()
    }
}

/// A small square icon button: dim until you point at it. `on` washes it with ember.
struct IconButton: View {
    let icon: String
    var on = false
    var help: String? = nil
    let action: () -> Void
    @State private var hovering = false
    init(_ icon: String, on: Bool = false, help: String? = nil, action: @escaping () -> Void) {
        self.icon = icon; self.on = on; self.help = help; self.action = action
    }
    var body: some View {
        Button(action: action) { IconFace(icon: icon, on: on, hovering: hovering) }
            .buttonStyle(.plain).focusEffectDisabled()
            .onHover { hovering = $0 }
            .help(help ?? "")
    }
}

struct IconFace: View {
    let icon: String
    var on = false
    var hovering = false
    var body: some View {
        Image(systemName: icon).font(.system(size: 12.5, weight: .medium))
            .foregroundStyle(on ? Theme.accent : hovering ? Theme.text : Theme.dim)
            .frame(width: 28, height: 28)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(on ? Theme.emberSoft : hovering ? Theme.hover : .clear))
            .contentShape(Rectangle())
    }
}

/// Section header: small, quiet, sentence case.
struct SectionLabel: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(Theme.ui(12, 600)).foregroundStyle(Theme.faint)
    }
}

/// A small rounded label: a count, a level, a state.
struct Chip: View {
    let text: String
    var fill: Color = Theme.panel
    var fg: Color = Theme.dim
    init(_ text: String, fill: Color = Theme.panel, fg: Color = Theme.dim) { self.text = text; self.fill = fill; self.fg = fg }
    var body: some View {
        Text(text).font(Theme.ui(11.5, 600)).monospacedDigit().foregroundStyle(fg).lineLimit(1)
            .padding(.horizontal, 7).padding(.vertical, 2.5)
            .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(fill))
    }
}

/// Title row shared by the main screens: the name of the screen, an optional quiet detail, and room for controls.
struct PageHeader<Trailing: View>: View {
    let title: String
    var detail: String? = nil
    @ViewBuilder var trailing: Trailing

    init(title: String, detail: String? = nil, @ViewBuilder trailing: () -> Trailing) {
        self.title = title; self.detail = detail; self.trailing = trailing()
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(title).font(Theme.headline).tracking(-0.4)
            if let d = detail { Text(d).font(Theme.ui(13, 500)).foregroundStyle(Theme.faint).monospacedDigit() }
            Spacer(minLength: 12)
            trailing
        }
        .padding(.horizontal, Page.gutter).padding(.top, 14).padding(.bottom, 16)
    }
}

extension PageHeader where Trailing == EmptyView {
    init(title: String, detail: String? = nil) { self.init(title: title, detail: detail) { EmptyView() } }
}

/// Page measurements every screen shares, so edges line up from one tab to the next.
enum Page {
    static let gutter: CGFloat = 28
    static let column: CGFloat = 760          // the widest a column of reading gets
}

/// A lifted block with a hairline edge: the container for a list of rows.
struct Card<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.raised)
                .shadow(color: Theme.shadow, radius: 14, y: 6))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Theme.line))
    }
}

extension View {
    /// One row inside a `Card`: standard padding and a hairline under it unless it is the last.
    func cardRow(last: Bool = false) -> some View {
        self.padding(.horizontal, 14).padding(.vertical, 11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(alignment: .bottom) { if !last { Theme.line.frame(height: 1).padding(.leading, 14) } }
    }

    /// A screen's scrolling column: gutters on both sides and a cap on how wide reading gets.
    func pageColumn() -> some View {
        self.frame(maxWidth: Page.column, alignment: .leading)
            .padding(.horizontal, Page.gutter)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A line of quiet text for "nothing here yet".
struct EmptyNote: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(Theme.body).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
    }
}

/// Two or three choices in one control; the chosen one lifts.
struct Segmented<T: Hashable>: View {
    let options: [(value: T, title: String)]
    @Binding var selection: T
    var body: some View {
        HStack(spacing: 2) {
            ForEach(options, id: \.value) { o in
                let on = selection == o.value
                Button { withAnimation(.easeOut(duration: 0.15)) { selection = o.value } } label: {
                    Text(o.title).font(Theme.ui(12.5, on ? 600 : 500)).foregroundStyle(on ? Theme.text : Theme.dim)
                        .padding(.horizontal, 11).padding(.vertical, 4)
                        .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(on ? Theme.raised : .clear)
                            .shadow(color: .black.opacity(on ? 0.10 : 0), radius: 1.5, y: 0.5))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).focusEffectDisabled()
            }
        }
        .padding(2)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Theme.panel))
    }
}

/// Her: EDITH's mark. A soft drop of iris ink that is never quite still. It breathes slowly when idle, opens up and
/// wobbles when she listens, turns while she thinks and ripples quickly while she talks. On the board it shrinks to
/// the nib that writes. (Called Orb because every screen already places it by that name.)
struct Orb: View {
    enum Mood { case idle, listening, thinking, speaking, off }
    var mood: Mood = .idle
    var size: CGFloat = 22
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion || mood == .off)) { tl in
            Canvas { ctx, sz in
                let t = tl.date.timeIntervalSinceReferenceDate
                let path = InkDrop.path(in: CGRect(origin: .zero, size: sz).insetBy(dx: sz.width * 0.1, dy: sz.height * 0.1),
                                        t: reduceMotion ? 0 : t, mood: mood)
                var shade = ctx
                shade.addFilter(.shadow(color: Theme.irisDeep.opacity(mood == .off ? 0 : 0.35), radius: sz.width * 0.08,
                                        x: 0, y: sz.height * 0.06))
                shade.fill(path, with: .linearGradient(
                    Gradient(colors: mood == .off ? [Theme.hex(0xB9BBC0), Theme.hex(0x8D9096)] : [Theme.irisLight, Theme.hex(0x4F46F0), Theme.irisDeep]),
                    startPoint: CGPoint(x: sz.width * 0.2, y: 0), endPoint: CGPoint(x: sz.width * 0.85, y: sz.height)))
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// The ink drop's outline: a circle pushed in and out by three slow waves. Pure, so it is the same at the same time.
enum InkDrop {
    static func path(in r: CGRect, t: Double, mood: Orb.Mood) -> Path {
        let (amp, speed, spin): (Double, Double, Double) = {
            switch mood {
            case .idle: return (0.045, 0.55, 0.05)
            case .listening: return (0.085, 1.5, 0.15)
            case .thinking: return (0.06, 0.9, 1.6)
            case .speaking: return (0.075, 3.2, 0.25)
            case .off: return (0.0, 0, 0)
            }
        }()
        let breathe = mood == .idle ? 1 + 0.035 * sin(t * 1.9) : mood == .listening ? 1.04 + 0.03 * sin(t * 4) : 1
        let c = CGPoint(x: r.midX, y: r.midY), base = min(r.width, r.height) / 2 * breathe
        var p = Path()
        let n = 72
        for i in 0...n {
            let a = Double(i) / Double(n) * 2 * .pi
            let w = 0.55 * sin(3 * a + t * speed + t * spin) + 0.35 * sin(2 * a - t * speed * 0.7)
                  + 0.2 * sin(5 * a + t * speed * 1.3)
            let rr = base * (1 + amp * w)
            let pt = CGPoint(x: c.x + rr * cos(a), y: c.y + rr * sin(a))
            if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
        }
        p.closeSubpath()
        return p
    }
}

/// One icon and one colourway per school subject, the same on every screen.
enum SubjectStyle {
    static func icon(_ s: String) -> String {
        switch s.lowercased() {
        case "physics": return "atom"
        case "chem", "chemistry": return "flask.fill"
        case "math", "maths": return "function"
        case "econ", "economics": return "chart.line.uptrend.xyaxis"
        case "english": return "book.fill"
        case "spanish": return "character.bubble.fill"
        default: return "graduationcap.fill"
        }
    }
    static func plastic(_ s: String) -> Plastic {
        switch s.lowercased() {
        case "physics": return Theme.blue
        case "chem", "chemistry": return Theme.green
        case "math", "maths": return Theme.limeSoft
        case "econ", "economics": return Theme.yellow
        case "english": return Theme.orange
        case "spanish": return Theme.yellow
        default: return Theme.grey
        }
    }
}
