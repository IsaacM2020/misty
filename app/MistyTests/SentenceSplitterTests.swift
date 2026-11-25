import XCTest
@testable import MistyCore

final class SentenceSplitterTests: XCTestCase {
    /// Feeds the pieces in order and returns every sentence emitted, flush included.
    private func split(_ pieces: [String], commaAfter: Int = 120) -> [String] {
        var s = SentenceSplitter(commaAfter: commaAfter)
        return pieces.flatMap { s.feed($0) } + s.flush()
    }

    func testSentencesAcrossPieces() {
        XCTAssertEqual(split(["Sure, I can", " help. What's the", " question you have? Let's go then, friend!"]),
                       ["Sure, I can help.", "What's the question you have?", "Let's go then, friend!"])
    }

    func testEmitsAsSoonAsTheNextPieceStarts() {
        var s = SentenceSplitter()
        XCTAssertEqual(s.feed("Got it, opening it now."), [])          // could still be "now.5"
        XCTAssertEqual(s.feed(" Opening"), ["Got it, opening it now."])
        XCTAssertEqual(s.flush(), ["Opening"])
    }

    func testNewlineCuts() {
        XCTAssertEqual(split(["First line here\nSecond line is here\nThird line is here"]), ["First line here Second line is here", "Third line is here"])
        XCTAssertEqual(split(["One two three four.\n\nFive six seven eight"]), ["One two three four.", "Five six seven eight"])
    }

    func testDecimalsStayWhole() {
        XCTAssertEqual(split(["It's 3.5 metres long. Then do 2 more."]), ["It's 3.5 metres long.", "Then do 2 more."])
        XCTAssertEqual(split(["That's 3.", "5 times bigger."]), ["That's 3.5 times bigger."])
    }

    func testAbbreviationsDoNotCut() {
        XCTAssertEqual(split(["Use a fruit, e.g. ", "a banana. Done."]), ["Use a fruit, e.g. a banana.", "Done."])
        XCTAssertEqual(split(["Ask Dr. Lee i.e. ", "the chem teacher."]), ["Ask Dr. Lee i.e. the chem teacher."])
    }

    func testListNumberDoesNotCutAlone() {
        XCTAssertEqual(split(["1. Open Desmos now please. 2. Type it in there."]), ["1. Open Desmos now please.", "2. Type it in there."])
    }

    func testRunsAndClosers() {
        XCTAssertEqual(split(["Wait... really?! Yes."]), ["Wait...", "really?! Yes."])      // complete short first sentence goes at once; short tail merges
        XCTAssertEqual(split(["She said \"go now.\" Then she left."]), ["She said \"go now.\"", "Then she left."])
        XCTAssertEqual(split(["Wait now, really sir?! Yes that is so."]), ["Wait now, really sir?!", "Yes that is so."])
    }

    func testCommaCutOnlyWhenLong() {
        XCTAssertEqual(split(["Short, sweet, done"]), ["Short, sweet, done"])
        let long = String(repeating: "word ", count: 26)   // 130 chars
        XCTAssertEqual(split([long + "and more, the rest of it now"]), [long + "and more,", "the rest of it now"])
        XCTAssertEqual(split(["abcdefghij klmnop qrs tuvw, xyz tuv wx yz ab"], commaAfter: 12), ["abcdefghij klmnop qrs tuvw,", "xyz tuv wx yz ab"])
    }

    func testFlushEmptyAndBlank() {
        var s = SentenceSplitter()
        XCTAssertEqual(s.flush(), [])
        XCTAssertEqual(s.feed("   \n "), [])
        XCTAssertEqual(s.flush(), [])
    }

    func testCommaBeforeAShortTailDoesNotCut() {
        let clause = String(repeating: "word ", count: 25) + "and so on"      // well past 120 chars
        XCTAssertEqual(split([clause + ", boss."]), [clause + ", boss."])
        XCTAssertEqual(split([clause + ", ", "boss.", " Next sentence is here."]), [clause + ", boss.", "Next sentence is here."])
        XCTAssertEqual(split([clause + ", you know, boss."]), [clause + ", you know, boss."])
        XCTAssertEqual(split([clause + ", boss. And then some more words."]), [clause + ", boss.", "And then some more words."])
    }

    func testFinalShortTailMergesIntoTheHeldChunk() {
        // chunk 1 goes out at once; chunk 2 is held so the short tail can join it
        var s = SentenceSplitter()
        XCTAssertEqual(s.feed("First sentence is long enough. Second sentence is long too. Third"), ["First sentence is long enough."])
        XCTAssertEqual(s.feed(" bit."), [])
        XCTAssertEqual(s.flush(), ["Second sentence is long too. Third bit."])
    }

    func testFirstSentenceIsNotDelayed() {
        var s = SentenceSplitter()
        XCTAssertEqual(s.feed("This first sentence is long enough. And"), ["This first sentence is long enough."])
        // a long first clause with a comma and plenty after it also leaves before the sentence ends
        var t = SentenceSplitter()
        let clause = String(repeating: "word ", count: 25) + "and so on"
        XCTAssertEqual(t.feed(clause + ", then more and more words keep coming "), [clause + ","])
    }

    func testShortFirstChunkMergesForward() {
        XCTAssertEqual(split(["Sure, boss."]), ["Sure, boss."])
        // a short first chunk that is not a complete sentence still waits for the next one
        XCTAssertEqual(split(["Sure\nOpening your essay now."]), ["Sure Opening your essay now."])
    }

    func testShortCompleteFirstSentenceIsEmittedImmediately() {
        var s = SentenceSplitter()
        XCTAssertEqual(s.feed("One sec. "), ["One sec."])
        XCTAssertEqual(s.feed("Your essay is open now and ready. "), [])
        XCTAssertEqual(s.flush(), ["Your essay is open now and ready."])
        XCTAssertEqual(split(["One sec. ", "Your essay is open now and ready."]), ["One sec.", "Your essay is open now and ready."])
    }

    func testNoChunkOfThreeWordsOrFewerUnlessItIsTheWholeReply() {
        let reply = ["Right, so here is the plan for tomorrow. ", "Ok. ", "Go. ", "Then read chapter four properly. ", "Done. ", "Yes."]
        var s = SentenceSplitter()
        let all = reply.flatMap { s.feed($0) } + s.flush()
        XCTAssertTrue(all.allSatisfy { SentenceSplitter.wordCount($0) >= 4 }, "\(all)")
        XCTAssertEqual(all.joined(separator: " "), reply.joined().trimmingCharacters(in: .whitespaces))
        XCTAssertEqual(split(["Done."]), ["Done."])
    }

    func testFlushIsIdempotentAndTheSplitterReusable() {
        var s = SentenceSplitter()
        _ = s.feed("One two three four. Five six seven eight. Nine")
        XCTAssertEqual(s.flush(), ["Five six seven eight. Nine"])
        XCTAssertEqual(s.flush(), [])
        XCTAssertEqual(s.flush(), [])
        XCTAssertEqual(s.feed("Fresh start of the next one. And"), ["Fresh start of the next one."])
    }
}
