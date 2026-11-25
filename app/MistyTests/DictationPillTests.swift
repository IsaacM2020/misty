import SwiftUI
import XCTest
@testable import MistyCore

/// V8: dictation uses live mode's light-glass card. Set MISTY_PILL_DIR to save a screenshot of every state.
@MainActor
final class DictationPillTests: XCTestCase {
    let sw: CGFloat = 1440

    func testEachPhaseMapsToItsCard() {
        XCTAssertEqual(DictationCard.from(phase: .recording(locked: false), problem: nil), .listening(locked: false, words: ""))
        XCTAssertEqual(DictationCard.from(phase: .recording(locked: true), problem: nil, words: "hi"), .listening(locked: true, words: "hi"))
        XCTAssertEqual(DictationCard.from(phase: .reading, problem: nil, words: "hi there"), .reading(words: "hi there"))
        XCTAssertEqual(DictationCard.from(phase: .done("Pasted"), problem: nil), .done("Pasted"))
        XCTAssertEqual(DictationCard.from(phase: .idle, problem: "Mic gave silence"), .problem("Mic gave silence"))
        XCTAssertEqual(DictationCard.from(phase: .recording(locked: false), problem: "Heard nothing"), .problem("Heard nothing"))
    }

    func testTheCardIsSmallWhileHeTalksAndReads() {
        let meter = DictationCard.listening(locked: false, words: "").size(screenWidth: sw)
        XCTAssertLessThanOrEqual(meter.width, 60)
        XCTAssertEqual(meter.height, PillLayout.oneLine)
        XCTAssertGreaterThan(DictationCard.listening(locked: true, words: "").size(screenWidth: sw).width, meter.width)
        XCTAssertEqual(DictationCard.reading(words: "").size(screenWidth: sw).height, PillLayout.oneLine)
    }

    func testOnceWordsAppearTheWidthHoldsStill() {
        let a = DictationCard.listening(locked: false, words: "so").size(screenWidth: sw)
        let b = DictationCard.listening(locked: false, words: "so I think we should go to the park after school today").size(screenWidth: sw)
        let c = DictationCard.reading(words: "so I think we should go").size(screenWidth: sw)
        XCTAssertEqual(a, b); XCTAssertEqual(b, c)
        XCTAssertEqual(DictationCard.listening(locked: false, words: "a").key, DictationCard.reading(words: "a b").key)
    }

    func testWordsGrowTheCardButNeverPastLiveModesMax() {
        let short = DictationCard.done("Pasted").size(screenWidth: sw)
        let long = DictationCard.problem(String(repeating: "Couldn't open the mic ", count: 20)).size(screenWidth: sw)
        XCTAssertLessThan(short.width, 120)
        XCTAssertLessThanOrEqual(long.width, PillLayout.maxWidth)
        XCTAssertEqual(short.height, PillLayout.oneLine)
    }

    func testShapesHaveDistinctKeys() {
        let keys = [DictationCard.listening(locked: false, words: ""), .listening(locked: true, words: ""), .reading(words: ""),
                    .listening(locked: false, words: "hi"), .done("a"), .problem("b")].map(\.key)
        XCTAssertEqual(Set(keys).count, 6)
        XCTAssertEqual(DictationCard.done("a").key, DictationCard.done("b").key)
    }

    func testEveryStateRendersAtItsCardSizeInLightAndDark() {
        let r = PillRenderTests()
        let talk: [Float] = [0.9, 0.7, 0.5, 0.3]
        let said = "so I think we should probably finish the physics sheet tonight"
        let states: [(String, DictationVM.Phase, String?, String)] = [
            ("d1-talking", .recording(locked: false), nil, ""),
            ("d2-words", .recording(locked: false), nil, said),
            ("d3-locked", .recording(locked: true), nil, said),
            ("d4-reading", .reading, nil, said),
            ("d5-pasted", .done("Pasted"), nil, ""),
            ("d6-problem", .idle, "Mic gave silence", "")]
        let screen = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame.width ?? 800
        for dark in [false, true] {
            for (name, phase, problem, words) in states {
                let vm = DictationVM(ears: Ears())
                vm.testShow(phase: phase, problem: problem, history: talk, words: words)
                let want = PillLayout.canvas(DictationCard.from(phase: phase, problem: problem, words: words).size(screenWidth: screen))
                let got = r.renderView(DictationPillView(vm: vm, flat: true), dark: dark, name: name)
                XCTAssertEqual(got.width, want.width, accuracy: 1, "\(name) width")
                XCTAssertEqual(got.height, want.height, accuracy: 1, "\(name) height")
            }
        }
    }
    // V10 (17 Nov 2026): a 58 s dictation showed only its last few words; hovering opens the card on all of them.
    func testHoveringOpensTheCardOnEverythingSaid() {
        let long = String(repeating: "and there is a limit for the voice transcription that cuts it off ", count: 8)
        let closed = DictationCard.listening(locked: false, words: long).size(screenWidth: sw)
        let open = DictationCard.listening(locked: false, words: long).size(screenWidth: sw, hovering: true)
        XCTAssertEqual(closed.height, PillLayout.oneLine)
        XCTAssertGreaterThan(open.height, PillLayout.lineHeight * 6)
        XCTAssertLessThanOrEqual(open.height, CGFloat(DictationCard.openLines) * PillLayout.lineHeight + 16)
        XCTAssertEqual(open.width, PillLayout.width(screenWidth: sw))
        XCTAssertGreaterThan(DictationCard.reading(words: long).size(screenWidth: sw, hovering: true).height, closed.height)
        // nothing said yet, or a status: hovering changes nothing
        XCTAssertEqual(DictationCard.listening(locked: false, words: "").size(screenWidth: sw, hovering: true),
                       DictationCard.listening(locked: false, words: "").size(screenWidth: sw))
        XCTAssertEqual(DictationCard.done("Pasted").size(screenWidth: sw, hovering: true), DictationCard.done("Pasted").size(screenWidth: sw))
    }

    func testTheOpenCardRendersAtItsSize() {
        let r = PillRenderTests()
        let long = String(repeating: "so I think we should probably finish the physics sheet tonight ", count: 6)
        let screen = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame.width ?? 800
        for (name, phase) in [("d7-open", DictationVM.Phase.recording(locked: false)), ("d8-open-locked", .recording(locked: true))] {
            let vm = DictationVM(ears: Ears())
            vm.testShow(phase: phase, history: [0.5, 0.4], words: long, hovering: true)
            let want = PillLayout.canvas(DictationCard.from(phase: phase, problem: nil, words: long).size(screenWidth: screen, hovering: true))
            let got = r.renderView(DictationPillView(vm: vm, flat: true), dark: false, name: name)
            XCTAssertEqual(got.width, want.width, accuracy: 1, "\(name) width")
            XCTAssertEqual(got.height, want.height, accuracy: 1, "\(name) height")
        }
    }
}
