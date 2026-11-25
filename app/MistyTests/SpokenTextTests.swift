import XCTest
@testable import MistyCore

/// Each case is a line the voice model read wrong on 2026-11-12 (spoken, then transcribed back), and what it should hear instead.
final class SpokenTextTests: XCTestCase {
    func testClockTimesAndDatesBecomeSayable() {
        XCTAssertEqual(SpokenText.clean("The test is at 22:15 tonight."), "The test is at 10:15 pm tonight.")
        XCTAssertEqual(SpokenText.clean("Dinner at 19:00, bed by 00:30."), "Dinner at 7 pm, bed by 12:30 am.")
        XCTAssertEqual(SpokenText.clean("It is due on 2026-11-18."), "It is due on 18 November.")
        XCTAssertEqual(SpokenText.clean("Class is 9:05 to 10:30."), "Class is 9:05 to 10:30.")       // already read right: left alone
        XCTAssertEqual(SpokenText.clean("The video is 1:30 long."), "The video is 1:30 long.")
    }

    func testLabelsScoresAndShortFormsBecomeWords() {
        XCTAssertEqual(SpokenText.clean("Revision Worksheet Mechanics_SA1 in Grade 11IB Physics 2026-27."),
                       "Revision Worksheet Mechanics SA1 in Grade 11 IB Physics 2026 to 27.")
        XCTAssertEqual(SpokenText.clean("City beat Inter 4-1, e.g. a big win."), "City beat Inter 4 to 1, for example a big win.")
        XCTAssertEqual(SpokenText.clean("Math HL vs Chem HL, notes, slides, etc."), "Math HL versus Chem HL, notes, slides, and so on.")
        XCTAssertEqual(SpokenText.clean("Give it 5-10 minutes."), "Give it 5 to 10 minutes.")
    }

    func testLinksMarkdownAndEmojiAreNotReadOut() {
        XCTAssertEqual(SpokenText.clean("Read it here: https://www.bbc.com/sport/football/articles/c1234xyz"),
                       "Read it here: a link on bbc dot com")
        XCTAssertEqual(SpokenText.clean("**Done.** Opened your `essay.docx` file. 🎉"), "Done. Opened your essay.docx file.")
    }

    func testPlainSpeechAndMathsPassUntouched() {
        for s in ["Good morning. You have Chem at seven.", "x squared minus 2 equals 14.", "That costs $4.50, about 30% less.",
                  "Hey Isaac, it's Misty. This is how I sound."] {
            XCTAssertEqual(SpokenText.clean(s), s)
        }
        XCTAssertEqual(SpokenText.clean("   "), "")
    }
}

/// V2: maths, symbols, lists and run-on sentences as a person says them. Every case is a pattern from his own voice replies
/// (the 27/48 question, the bond order 4 over 3, "27 °C", the nitrate and heat questions, the study-mode list) or made up to
/// cover the same symbol in another place.
final class SpokenTextMathTests: XCTestCase {
    func said(_ s: String) -> String { SpokenText.clean(s) }

    func testFractions() {
        XCTAssertEqual(said("27/48 reduces to 9/16."), "27 over 48 reduces to 9 over 16.")
        XCTAssertEqual(said("The bond order is 4/3, about 1.33."), "The bond order is 4 over 3, about 1.33.")
        XCTAssertEqual(said("You have 1/2 a page left."), "You have 1 over 2 a page left.")
        XCTAssertEqual(said("Simplify 3x/(x-2) first."), "Simplify 3x over (x minus 2) first.")
        XCTAssertEqual(said("Either yes and/or no works."), "Either yes and/or no works.")                  // not maths
    }

    func testPowersAndRoots() {
        XCTAssertEqual(said("x^2 + 3x = 10"), "x squared plus 3x equals 10")
        XCTAssertEqual(said("a^3 and b^3"), "a cubed and b cubed")
        XCTAssertEqual(said("2^n grows fast, x^10 faster."), "2 to the power n grows fast, x to the power 10 faster.")
        XCTAssertEqual(said("x² - 4 factorises."), "x squared minus 4 factorises.")
        XCTAssertEqual(said("The root is √16 which is 4."), "The root is square root of 16 which is 4.")
        XCTAssertEqual(said("Area is π r²."), "Area is pi r squared.")
    }

    func testOperatorsAndRelations() {
        XCTAssertEqual(said("y = 2x + 1"), "y equals 2x plus 1")
        XCTAssertEqual(said("3 × 4 = 12 and 12 ÷ 4 = 3"), "3 times 4 equals 12 and 12 divided by 4 equals 3")
        XCTAssertEqual(said("3*4 is 12."), "3 times 4 is 12.")
        XCTAssertEqual(said("It is ≈ 1.33 per link."), "It is about 1.33 per link.")
        XCTAssertEqual(said("x ≠ 2, x ≥ 0 and y ≤ 5."), "x is not equal to 2, x greater than or equal to 0 and y less than or equal to 5.")
        XCTAssertEqual(said("Then x < 2 and y > 3."), "Then x less than 2 and y greater than 3.")
        XCTAssertEqual(said("x-2 is zero when x = 2."), "x minus 2 is zero when x equals 2.")
        XCTAssertEqual(said("Done - the file is open."), "Done - the file is open.")                           // a dash in prose stays
    }

    func testUnits() {
        XCTAssertEqual(said("Silver starts at 27 °C."), "Silver starts at 27 degrees Celsius.")
        XCTAssertEqual(said("Turn it 90° left, or 5 °F colder."), "Turn it 90 degrees left, or 5 degrees Fahrenheit colder.")
        XCTAssertEqual(said("That is 5.0% silver, 19 times heavier."), "That is 5.0% silver, 19 times heavier.")
    }

    func testListsAndMarkdownBecomeSentences() {
        XCTAssertEqual(said("Here is what study mode does:\n- Hide WhatsApp and Spotify\n- Start a 25-minute timer"),
                       "Here is what study mode does: Hide WhatsApp and Spotify. Start a 25-minute timer")
        XCTAssertEqual(said("1. First, factor it.\n2. Then solve for x."), "First, factor it. Then solve for x.")
        XCTAssertEqual(said("• Open the paper\n• Read question one"), "Open the paper. Read question one")
        XCTAssertEqual(said("## Plan\nStart with part (a)."), "Plan. Start with part (a).")
        XCTAssertEqual(said("**Step one:** write it down."), "Step one: write it down.")
        XCTAssertEqual(said("Line one.\nLine two."), "Line one. Line two.")
    }

    func testPunctuationThatReadsBadly() {
        XCTAssertEqual(said("That works — mostly."), "That works, mostly.")
        XCTAssertEqual(said("Check it; then move on."), "Check it. then move on.")
    }

    func testALongSentenceIsSplitAtAConjunction() {
        let long = "Twenty seven over forty eight reduces to nine over sixteen because both numbers divide by three, and the fraction is the same number written with smaller parts, so you can use either one."
        let out = said(long)
        XCTAssertTrue(out.contains("three. and the fraction") || out.contains("three. And the fraction"), out)
        XCTAssertTrue(out.split(separator: ".").allSatisfy { $0.split(separator: " ").count <= 30 }, out)
        XCTAssertEqual(said("Short one, and another."), "Short one, and another.")                              // short: untouched
    }

    func testSplitLongNeverLeavesAShortPiece() {
        let long = "Twenty seven over forty eight reduces to nine over sixteen because both numbers divide by three, and then yes, so ok, but fine, and the fraction is the same number written with smaller parts, so you can use either one, boss."
        for s in [long, "I think so, and yes, boss.", "One two three four five six seven eight, and then nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twentyone twentytwo twentythree twentyfour twentyfive twentysix twentyseven, so yes."] {
            for piece in SpokenText.splitLong(s).split(whereSeparator: { ".!?".contains($0) }) {
                XCTAssertGreaterThanOrEqual(piece.split(separator: " ").count, 4, "\(piece) in \(SpokenText.splitLong(s))")
            }
        }
    }

    func testPlainWordsAndTheOldRulesStillPass() {
        for s in ["Good morning, you have Chem at seven.", "The answer is nine over sixteen.", "It costs about $4.50 a week.",
                  "Open page 18 of 41, then read it.", "Meet at 5 pm, bring a pen."] {
            XCTAssertEqual(said(s), s)
        }
    }
}
