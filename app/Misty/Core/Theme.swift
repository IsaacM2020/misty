import SwiftUI
import AppKit

/// EDITH's look in one place (2026-11-15, "Ink"). Clean, matte and calm: a cool soft white, near-black ink, and one
/// living colour, iris, kept for her (the mark), for what is selected and for the one thing to press. No glass: depth
/// comes from soft offset shadows. Follows the Mac's light or dark setting. Views use these, never their own colours.
enum Theme {
    static func hex(_ v: UInt32, _ a: Double = 1) -> Color {
        Color(.sRGB, red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255, opacity: a)
    }
    static func ns(_ v: UInt32, _ a: Double = 1) -> NSColor {
        NSColor(srgbRed: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255, alpha: a)
    }
    /// One colour for light, one for dark; it switches with the window.
    static func dyn(_ light: UInt32, _ dark: UInt32, _ la: Double = 1, _ da: Double? = nil) -> Color {
        Color(nsColor: NSColor(name: nil) { ap in
            ap.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? ns(dark, da ?? la) : ns(light, la)
        })
    }

    // Surfaces
    static let bg = dyn(0xF6F7F6, 0x121314)              // the page: a cool soft white
    static let desk = dyn(0xEDEFEE, 0x18191B)            // the rail
    static let panel = dyn(0xECEEED, 0x202224)           // sunken: fields, wells, quiet buttons
    static let raised = dyn(0xFFFFFF, 0x1B1C1E)          // lifted: cards, the composer, the board
    static let ink = dyn(0x15171A, 0xF1F2F3)             // text, and the fill of the main button
    static let onInk = dyn(0xFFFFFF, 0x121314)           // lettering on an ink fill
    static let night = hex(0x15171A)                     // always dark: the floating pills over other apps
    static let shadow = dyn(0x1B2430, 0x000000, 0.10, 0.45)   // soft lift under raised things

    // Text and lines
    static let text = ink
    static let dim = dyn(0x15171A, 0xF1F2F3, 0.62)
    static let faint = dyn(0x15171A, 0xF1F2F3, 0.42)
    static let line = dyn(0x15171A, 0xF1F2F3, 0.08, 0.10)
    static let hover = dyn(0x15171A, 0xF1F2F3, 0.045, 0.07)

    /// Iris: her colour. `ember` is the fill, `accent` the text-safe version, `emberSoft` a wash behind things.
    /// (The names stay from the old look so every screen picks the new colour up.)
    static let ember = dyn(0x4F46F0, 0x8B83FF)
    static let emberSoft = dyn(0x4F46F0, 0x8B83FF, 0.10, 0.18)
    static let accent = dyn(0x3B32D6, 0xA59EFF)
    static let emberNS = ns(0x4F46F0)
    static let irisLight = hex(0x8F88FF)                 // the lit side of her mark
    static let irisDeep = hex(0x3027C9)                  // the shaded side
    static let good = dyn(0x13804F, 0x4FD08F)
    static let urgent = dyn(0xD2352B, 0xFF6F61)
    static let warn = dyn(0xB25A09, 0xF0A64E)

    // Names the older screens still use. They land on the new palette.
    static let lime = ember
    static let limeNS = emberNS
    static let accentNS = emberNS                        // Stream's cursor

    // Button and tile colourways. `edge` is a hairline, `fg` the lettering on top.
    static let black = Plastic(fill: ink, edge: .clear, fg: onInk)
    static let limeFill = Plastic(fill: ember, edge: .clear, fg: .white)
    static let limeSoft = Plastic(fill: emberSoft, edge: .clear, fg: accent)
    static let light = Plastic(fill: panel, edge: .clear, fg: ink)
    static let grey = Plastic(fill: panel, edge: .clear, fg: dim)
    // Where a thing came from: a soft wash and a strong glyph.
    static let orange = Plastic(fill: dyn(0xE5484D, 0xFF6369, 0.12, 0.18), edge: .clear, fg: dyn(0xCD2B31, 0xFF8589))   // mail
    static let blue = Plastic(fill: dyn(0x2F6FED, 0x5B9BFF, 0.12, 0.18), edge: .clear, fg: dyn(0x1F5BD8, 0x7FB0FF))     // calendar
    static let green = Plastic(fill: dyn(0x1E9E5A, 0x3DD68C, 0.13, 0.18), edge: .clear, fg: dyn(0x18794A, 0x54C68A))    // WhatsApp
    static let yellow = Plastic(fill: dyn(0xD98A00, 0xF5B83D, 0.15, 0.18), edge: .clear, fg: dyn(0x99600A, 0xF5C262))   // Messages

    // Type: the system face (SF Pro). The old numeric weights map onto its real weights.
    static func ui(_ size: CGFloat, _ weight: CGFloat = 400, width: CGFloat = 100) -> Font {
        let w: Font.Weight = weight < 470 ? .regular : weight < 580 ? .medium : weight < 700 ? .semibold : .bold
        return .system(size: size, weight: w)
    }
    static let body = ui(13.5, 400)
    static let small = ui(12, 400)
    static let label = ui(11.5, 600)
    static let title = ui(17, 650)
    static let headline = ui(24, 700)
    static let wordmark = Font.system(size: 17, weight: .heavy)
    static let mono = Font.system(size: 12, design: .monospaced)

    static func registerFonts() {}                        // nothing to load any more: the system face is always there
}

/// One fill colourway.
struct Plastic: Sendable {
    let fill: Color
    let edge: Color
    let fg: Color
}
