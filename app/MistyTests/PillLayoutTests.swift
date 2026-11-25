import SwiftUI
import XCTest
@testable import MistyCore

/// V1: the voice pill shows her whole reply. The layout rules are pure, so no screen is needed.
@MainActor
final class PillLayoutTests: XCTestCase {
    let long = "Twenty seven over forty eight reduces to nine over sixteen, because both divide by three. "
        + "So the fraction is the same number, boss, just written in smaller parts."

    // ---- size ----------------------------------------------------------------------------------------------------
    func testWidthHasAFloorAndACeilingAndFitsASmallScreen() {
        XCTAssertEqual(PillLayout.width(screenWidth: 3000), PillLayout.maxWidth)
        XCTAssertEqual(PillLayout.width(screenWidth: 1440), PillLayout.maxWidth)
        XCTAssertEqual(PillLayout.width(screenWidth: 400), 360)                       // screen minus a margin each side
        XCTAssertEqual(PillLayout.width(screenWidth: 200), PillLayout.minWidth)
        XCTAssertGreaterThan(PillLayout.textWidth(screenWidth: 1440), 250)
    }

    func testAReplyGetsSixLinesAndEverythingElseTwo() {
        XCTAssertEqual(PillLayout.lineCap(reply: true), 6)
        XCTAssertEqual(PillLayout.lineCap(reply: false), 2)
    }

    func testTextHeightGrowsWithTheTextUpToTheCap() {
        let l = PillLayout.lineHeight
        XCTAssertEqual(PillLayout.textHeight(content: 0, reply: true), l)             // never less than one line
        XCTAssertEqual(PillLayout.textHeight(content: l, reply: true), l)
        XCTAssertEqual(PillLayout.textHeight(content: l * 3, reply: true), l * 3)
        XCTAssertEqual(PillLayout.textHeight(content: l * 20, reply: true), l * 6)    // capped, the rest scrolls
        XCTAssertEqual(PillLayout.textHeight(content: l * 5, reply: false), l * 2)    // transcript: two lines
    }

    func testItScrollsOnlyWhenTheTextIsTallerThanTheBox() {
        let l = PillLayout.lineHeight
        XCTAssertFalse(PillLayout.scrolls(content: l * 6, reply: true))
        XCTAssertTrue(PillLayout.scrolls(content: l * 7, reply: true))
        XCTAssertFalse(PillLayout.scrolls(content: l * 2, reply: false))
        XCTAssertTrue(PillLayout.scrolls(content: l * 3, reply: false))
    }

    func testCornersStayRoundedButNeverBecomeAnEgg() {
        XCTAssertEqual(PillLayout.corner(height: 36), 18)
        XCTAssertEqual(PillLayout.corner(height: 150), 20)
    }

    // ---- never off screen ----------------------------------------------------------------------------------------
    func testAFrameThatRunsOffTheBottomIsPulledBackOnScreen() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let f = PillLayout.clamp(CGRect(x: 500, y: -40, width: 480, height: 160), into: screen)
        XCTAssertGreaterThanOrEqual(f.minY, 12)
        XCTAssertTrue(screen.contains(f))
    }

    func testAFrameOffTheRightEdgeSlidesLeft() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let f = PillLayout.clamp(CGRect(x: 1300, y: 700, width: 480, height: 60), into: screen)
        XCTAssertLessThanOrEqual(f.maxX, screen.maxX - PillLayout.edge)
        XCTAssertEqual(f.width, 480)
    }

    func testAFrameBiggerThanTheScreenIsShrunk() {
        let screen = CGRect(x: 0, y: 0, width: 300, height: 200)
        let f = PillLayout.clamp(CGRect(x: 0, y: 0, width: 480, height: 400), into: screen)
        XCTAssertTrue(screen.contains(f))
    }

    func testAFrameThatFitsIsLeftAlone() {
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let r = CGRect(x: 500, y: 700, width: 300, height: 44)
        XCTAssertEqual(PillLayout.clamp(r, into: screen), r)
    }

    func testASecondScreenWithAnOffsetOriginWorks() {
        let screen = CGRect(x: -1920, y: 100, width: 1920, height: 1080)
        let f = PillLayout.clamp(CGRect(x: -2000, y: 1100, width: 480, height: 200), into: screen)
        XCTAssertTrue(screen.contains(f))
    }

    // ---- what it shows -------------------------------------------------------------------------------------------
    func testWhileSpeakingTheWholeReplyShowsNotTheLastSentence() {
        var r = ""
        for s in ["Twenty seven over forty eight is nine over sixteen.", "Both divide by three.", "So it is the same number, boss."] {
            r = PillLayout.appended(r, s)
        }
        let shown = PillLayout.shown(state: .speaking, line: "So it is the same number, boss.", reply: r, lingering: false)
        XCTAssertTrue(shown.isReply)
        XCTAssertEqual(shown.text, "Twenty seven over forty eight is nine over sixteen. Both divide by three. So it is the same number, boss.")
    }

    func testItStaysAfterSheStopsUntilHeTalksAgain() {
        let r = long
        XCTAssertEqual(PillLayout.shown(state: .idle, line: "", reply: r, lingering: true).text, r)
        XCTAssertEqual(PillLayout.shown(state: .listening, line: "", reply: r, lingering: true).text, r)
        // he starts talking: his words win
        let him = PillLayout.shown(state: .listening, line: "and what about tomorrow", reply: r, lingering: true)
        XCTAssertEqual(him.text, "and what about tomorrow")
        XCTAssertFalse(him.isReply)
        // linger over: nothing of the reply
        XCTAssertEqual(PillLayout.shown(state: .listening, line: "", reply: r, lingering: false).text, "")
    }

    func testThinkingShowsHisWordsNotHerLastReply() {
        let s = PillLayout.shown(state: .thinking, line: "what is 27 over 48", reply: "old reply", lingering: false)
        XCTAssertEqual(s.text, "what is 27 over 48")
        XCTAssertFalse(s.isReply)
    }

    func testAppendedSkipsBlankSentencesAndJoinsWithOneSpace() {
        XCTAssertEqual(PillLayout.appended("", "  Hello. "), "Hello.")
        XCTAssertEqual(PillLayout.appended("Hello.", "   "), "Hello.")
        XCTAssertEqual(PillLayout.appended("Hello.", "World."), "Hello. World.")
    }
}

/// The reply is kept by the voice code, and a click on the pill opens Chat.
@MainActor
final class PillReplyTests: XCTestCase {
    func make() -> VoiceVM {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.speakReplies = true
        return v
    }

    func testClickingTheIdlePillOpensChatOnlyWhenThereIsSomethingToOpen() {
        let v = make()
        var opened = 0
        v.onOpenChat = { opened += 1 }
        v.pillClicked()
        XCTAssertEqual(opened, 0)                     // nothing was said: nothing to open
    }

    func testStateWordShowsEdithWhileTheReplyLingers() {
        let v = make()
        XCTAssertEqual(v.stateWord, "")
        XCTAssertFalse(v.lingering)
        XCTAssertTrue(v.reply.isEmpty)
    }
}

/// V6: the light glass card. Which mode it is in, how big it is, and when it opens.
@MainActor
final class PillModeTests: XCTestCase {
    func m(_ st: VoiceState, line: String = "", reply: String = "", linger: Bool = false, problem: String? = nil) -> PillLayout.Mode {
        PillLayout.mode(state: st, line: line, reply: reply, lingering: linger, problem: problem)
    }

    func testListeningForHerNameIsJustADot() {
        XCTAssertEqual(m(.listening), .dot)
        XCTAssertEqual(m(.starting), .dot)
        XCTAssertEqual(m(.idle), .dot)
    }

    func testHearingHimPassesAllHisWordsTheCardShowsTheNewest() {
        XCTAssertEqual(m(.listening, line: "and what about tomorrow in the afternoon after school"),
                       .hearing("and what about tomorrow in the afternoon after school"))
        let shown = LiveWords.words("one two three four five six seven eight nine ten eleven", keep: 9)
        XCTAssertEqual(shown.map(\.text).first, "three")
        XCTAssertEqual(shown.count, 9)
        // a word the model corrects re-arrives; the others keep their identity (and don't move)
        let a = LiveWords.words("open the maths", keep: 9), b = LiveWords.words("open the math sheet", keep: 9)
        XCTAssertEqual(a[0].id, b[0].id); XCTAssertNotEqual(a[2].id, b[2].id)
        XCTAssertEqual(m(.listening, line: "open maths"), .hearing("open maths"))
    }

    func testThinkingIsThreeDotsWhateverTheLine() {
        XCTAssertEqual(m(.thinking, line: "what is 27 over 48"), .thinking)
    }

    func testSpeakingShowsOnlyTheSentenceSheIsSayingNow() {
        XCTAssertEqual(m(.speaking, line: "Both divide by three.", reply: "27 over 48 is 9 over 16. Both divide by three."), .speaking("Both divide by three."))
        XCTAssertEqual(m(.speaking, line: "", reply: "First. Second one here."), .speaking("Second one here."))
        XCTAssertEqual(m(.speaking), .dot)
    }

    func testAfterSheFinishesTheLastSentenceStaysThenItIsADotAgain() {
        XCTAssertEqual(m(.idle, reply: "First. The last one.", linger: true), .done("The last one."))
        XCTAssertEqual(m(.listening, reply: "First. The last one.", linger: true), .done("The last one."))
        XCTAssertEqual(m(.listening, reply: "First. The last one.", linger: false), .dot)
        // he talks again: his words win
        XCTAssertEqual(m(.listening, line: "thanks", reply: "x. y.", linger: true), .hearing("thanks"))
    }

    func testAProblemOrANoteShowsAsText() {
        XCTAssertEqual(m(.listening, problem: "Mic is off: Settings > Permissions"), .note("Mic is off: Settings > Permissions"))
        XCTAssertEqual(m(.starting, line: "Loading her ears, about a minute"), .note("Loading her ears, about a minute"))
    }

    func testLastSentenceKeepsDecimalsAndHandlesOneSentence() {
        XCTAssertEqual(PillLayout.lastSentence("It is about 1.33 per link."), "It is about 1.33 per link.")
        XCTAssertEqual(PillLayout.lastSentence("One. Two? Three!"), "Three!")
        XCTAssertEqual(PillLayout.lastSentence("  "), "")
    }

    // ---- size ----------------------------------------------------------------------------------------------------
    func testADotIsASmallCircleAndThreeDotsAreAShortPill() {
        let d = PillLayout.cardSize(mode: .dot, expanded: nil, screenWidth: 1440)
        XCTAssertEqual(d, CGSize(width: 28, height: 28))
        XCTAssertEqual(PillLayout.cardCorner, 14)                                  // 14 pt corners: a 28 pt dot is a circle
        let t = PillLayout.cardSize(mode: .thinking, expanded: nil, screenWidth: 1440)
        XCTAssertEqual(t.height, PillLayout.oneLine)
        XCTAssertGreaterThan(t.width, 28)
    }

    func testWhileHeTalksTheCardIsOneSteadyWidth() {
        let a = PillLayout.cardSize(mode: .hearing("so"), expanded: nil, screenWidth: 1440)
        let b = PillLayout.cardSize(mode: .hearing("so I think we should go to the park after school"), expanded: nil, screenWidth: 1440)
        XCTAssertEqual(a, b)
    }

    func testWhileSheSpeaksTheCardNeverShrinksBackForAShortSentence() {
        let reply = "Twenty seven over forty eight is nine over sixteen. Yes."
        let long = PillLayout.cardSize(mode: .speaking("Twenty seven over forty eight is nine over sixteen."), expanded: nil, screenWidth: 1440, reply: reply)
        let short = PillLayout.cardSize(mode: .speaking("Yes."), expanded: nil, screenWidth: 1440, reply: reply)
        XCTAssertEqual(long, short)
        XCTAssertEqual(PillLayout.sentences("A. B? C"), ["A.", "B?", "C"])
    }

    func testOneLineCardsGrowWithTheWordsButNeverPastTheMaxWidth() {
        let a = PillLayout.cardSize(mode: .speaking("Okay."), expanded: nil, screenWidth: 1440)
        let b = PillLayout.cardSize(mode: .speaking("Twenty seven over forty eight is nine over sixteen."), expanded: nil, screenWidth: 1440)
        let c = PillLayout.cardSize(mode: .speaking(String(repeating: "word ", count: 80)), expanded: nil, screenWidth: 1440)
        XCTAssertEqual(a.height, PillLayout.oneLine)
        XCTAssertGreaterThan(b.width, a.width)
        XCTAssertEqual(c.width, PillLayout.maxWidth)
        XCTAssertEqual(c.height, PillLayout.oneLine)                               // one line, however long the sentence
        XCTAssertEqual(PillLayout.cardSize(mode: .speaking(String(repeating: "word ", count: 80)), expanded: nil, screenWidth: 300).width,
                       PillLayout.width(screenWidth: 300))                         // a small screen narrows it
    }

    func testHoveringOnAReplyOpensItToTheWholeReplyUpToSixLines() {
        let reply = String(repeating: "Twenty seven over forty eight is nine over sixteen, boss. ", count: 10)
        XCTAssertNil(PillLayout.expandedText(mode: .speaking("x."), reply: reply, hovering: false))
        XCTAssertNil(PillLayout.expandedText(mode: .thinking, reply: reply, hovering: true))
        XCTAssertNil(PillLayout.expandedText(mode: .dot, reply: reply, hovering: true))
        XCTAssertNil(PillLayout.expandedText(mode: .speaking("x."), reply: "", hovering: true))
        XCTAssertEqual(PillLayout.expandedText(mode: .done("x."), reply: reply, hovering: true), reply)
        let open = PillLayout.cardSize(mode: .speaking("x."), expanded: reply, screenWidth: 1440)
        XCTAssertEqual(open.width, PillLayout.maxWidth)
        XCTAssertEqual(open.height, 6 * PillLayout.lineHeight + 18)                // capped at six lines, the rest scrolls
        let short = PillLayout.cardSize(mode: .speaking("x."), expanded: "Short reply here.", screenWidth: 1440)
        XCTAssertEqual(short.height, PillLayout.lineHeight + 18)
    }

    func testTheWindowIsTheCardPlusAHairAndKeepsOnScreen() {
        let card = CGSize(width: 480, height: 120)
        XCTAssertEqual(PillLayout.canvas(card), CGSize(width: 482, height: 122))
        let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let f = PillLayout.clamp(CGRect(x: 1400, y: 5, width: 482, height: 122), into: screen)
        XCTAssertTrue(screen.contains(f))
    }

    func testScrollFollowsTheNewestWordsUntilHeScrollsUpAndNeverPastTheTop() {
        XCTAssertEqual(PillLayout.scrollOffset(requested: 0, contentHeight: 300, boxHeight: 102), 0)
        XCTAssertEqual(PillLayout.scrollOffset(requested: 50, contentHeight: 300, boxHeight: 102), 50)
        XCTAssertEqual(PillLayout.scrollOffset(requested: 900, contentHeight: 300, boxHeight: 102), 198)
        XCTAssertEqual(PillLayout.scrollOffset(requested: 40, contentHeight: 60, boxHeight: 102), 0)
        XCTAssertEqual(PillLayout.scrollOffset(requested: -5, contentHeight: 300, boxHeight: 102), 0)
    }

    func testSameShapeKeepsTheSameKeyAndNewShapesDiffer() {
        XCTAssertEqual(PillLayout.Mode.speaking("a").key, PillLayout.Mode.speaking("b").key)      // only the words swap
        XCTAssertNotEqual(PillLayout.Mode.speaking("a").key, PillLayout.Mode.done("a").key)
        XCTAssertEqual(Set(["dot", "hearing", "thinking", "speaking", "done", "note"]).count, 6)
    }
}

/// The real card, drawn in a real hosting view in light and dark (the blur needs a real screen, so the card is flat here).
/// Set MISTY_PILL_DIR to also save a screenshot of every state.
@MainActor
final class PillRenderTests: XCTestCase {
    let reply = "Twenty seven over forty eight reduces to nine over sixteen. Both numbers divide by three. So it is the same number, written with smaller parts. You can use either one, whichever is easier to work with. Want to try one yourself?"

    func vm(_ st: VoiceState, line: String = "", reply: String = "", linger: Bool = false, hover: Bool = false) -> VoiceVM {
        let api = FakeAPI(); let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.testShow(state: st, line: line, reply: reply, lingering: linger); v.pillHover = hover
        return v
    }

    func render(_ v: VoiceVM, dark: Bool, name: String) -> CGSize { renderView(VoicePillView(vm: v, flat: true), dark: dark, name: name) }

    func renderView<V: View>(_ view: V, dark: Bool, name: String) -> CGSize {
        let host = NSHostingView(rootView: view.fixedSize())
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 260), styleMask: .borderless, backing: .buffered, defer: false)
        w.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        w.contentView = host
        for _ in 0..<4 { host.layoutSubtreeIfNeeded(); RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
        let size = host.fittingSize
        if let dir = ProcessInfo.processInfo.environment["MISTY_PILL_DIR"], name.count > 1 {
            // the card on a desktop-like backdrop, top centre like the real pill
            host.frame = NSRect(origin: .zero, size: size)
            host.layoutSubtreeIfNeeded()
            guard let card = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return size }
            host.cacheDisplay(in: host.bounds, to: card)
            let pad: CGFloat = 24, scale: CGFloat = 2
            let W = 560 * scale, H = (size.height + 2 * pad) * scale
            guard let out = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(W), pixelsHigh: Int(H), bitsPerSample: 8, samplesPerPixel: 4,
                                             hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
                  let ctx = NSGraphicsContext(bitmapImageRep: out) else { return size }
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = ctx
            let cols = dark ? [NSColor(red: 0.10, green: 0.11, blue: 0.20, alpha: 1), NSColor(red: 0.22, green: 0.12, blue: 0.30, alpha: 1)]
                            : [NSColor(red: 0.80, green: 0.88, blue: 0.98, alpha: 1), NSColor(red: 0.96, green: 0.86, blue: 0.90, alpha: 1)]
            NSGradient(colors: cols)?.draw(in: NSRect(x: 0, y: 0, width: W, height: H), angle: 20)
            let img = NSImage(size: size); img.addRepresentation(card)
            img.draw(in: NSRect(x: (W - size.width * scale) / 2, y: pad * scale, width: size.width * scale, height: size.height * scale))
            NSGraphicsContext.restoreGraphicsState()
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? out.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: "\(dir)/\(name)-\(dark ? "dark" : "light").png"))
        }
        return size
    }

    func states() -> [(String, VoiceVM)] {
        [("1-dot", vm(.listening)),
         ("2-hearing", vm(.listening, line: "and what about tomorrow in the afternoon after school")),
         ("3-thinking", vm(.thinking, line: "what is 27 over 48")),
         ("2b-tap-hint", vm(.listening, line: "Tap again to send")),
         ("4-speaking", vm(.speaking, line: "Both numbers divide by three.", reply: reply)),
         ("5-hover-reply", vm(.speaking, line: "Both numbers divide by three.", reply: reply, hover: true)),
         ("6-done", vm(.listening, reply: reply, linger: true)),
         ("7-note", vm(.starting, line: "Loading her ears, about a minute"))]
    }

    func testEveryStateRendersAtItsCardSizeInLightAndDark() {
        for dark in [false, true] {
            for (name, v) in states() {
                let sw = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame.width ?? 800
                let mode = v.pillMode
                let open = PillLayout.expandedText(mode: mode, reply: v.reply, hovering: v.pillHover)
                let want = PillLayout.canvas(PillLayout.cardSize(mode: mode, expanded: open, screenWidth: sw, reply: v.reply))
                let got = render(v, dark: dark, name: name)
                XCTAssertEqual(got.width, want.width, accuracy: 1, "\(name) width")
                XCTAssertEqual(got.height, want.height, accuracy: 1, "\(name) height")
                XCTAssertLessThanOrEqual(got.width, PillLayout.maxWidth + 2)
            }
        }
    }

    func testTheCardIsSmallAtRestAndOnlyTallWhenHovered() {
        let rest = render(vm(.speaking, line: "Both numbers divide by three.", reply: reply), dark: false, name: "x")
        let open = render(vm(.speaking, line: "Both numbers divide by three.", reply: reply, hover: true), dark: false, name: "y")
        XCTAssertLessThan(rest.height, 40)
        XCTAssertGreaterThan(open.height, 60)
        XCTAssertLessThanOrEqual(open.height, 6 * PillLayout.lineHeight + 18 + 2)
        XCTAssertLessThan(render(vm(.listening), dark: false, name: "z").width, 40)                 // a dot
    }
}
