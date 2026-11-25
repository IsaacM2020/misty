import XCTest
@testable import MistyCore

/// F2: end the turn at 350 ms of quiet when the words are plainly finished, keep 650 ms when they look cut off.
final class EarlyEndTests: XCTestCase {

    // MARK: the 60 made-up phrases (30 finished, 30 cut off). Nothing here is Isaac's real speech.

    static let finished: [String] = [
        "What time is it?",
        "Open my mail.",
        "What's on my calendar tomorrow?",
        "Add milk to my shopping list.",
        "Set a timer for ten minutes.",
        "Play some music.",
        "How do I convert a fraction to a decimal?",
        "Remind me to call my mum at six.",
        "What is two plus two?",
        "Turn the volume down.",
        "Can you read that again?",
        "Who wrote Pride and Prejudice?",
        "Quiz me on photosynthesis.",
        "Show me my homework for Friday.",
        "Is it going to rain today?",
        "Thanks, that is all I needed.",
        "Close the browser window.",
        "Explain the second law of thermodynamics.",
        "What is the capital of Spain?",
        "Send an email to my teacher about the deadline.",
        "Please open the chemistry notes.",
        "Where did I put my physics notes?",
        "Start the lesson on electrolysis.",
        "What does this word mean?",
        "Check my messages.",
        "Why is the sky blue?",
        "Summarise this page for me.",
        "Can you help me with my essay?",
        "Create a new reminder for tomorrow morning.",
        "That sounds good to me.",
    ]

    static let cutOff: [String] = [
        "Tell me a paragraph about the",
        "Open my",
        "What is the capital of",
        "Remind me to",
        "Set a timer for",
        "I want to ask you about",
        "Add apples, oranges,",
        "Play some music and",
        "Yes",
        "Okay",
        "My number is 9 1 2",
        "What time is",
        "Can you",
        "Send an email to",
        "Open the chemistry notes and",
        "So",
        "Hey, um,",
        "Tell me",
        "I was thinking that maybe",
        "Show me my homework for",
        "Where did I put my",
        "Because",
        "What is the",
        "Could you please",
        "Add milk, eggs, bread",
        "Call 0 4 1 2",
        "Open mail, then the",
        "The thing is...",
        "Look at the",
        "Do you know what",
    ]

    func testSixtyPhrasesJudgedAtLeastFiftyFiveRight() {
        var wrong: [String] = []
        for p in Self.finished where !VoiceIntent.looksFinished(p) { wrong.append("should be finished: \(p)") }
        for p in Self.cutOff where VoiceIntent.looksFinished(p) { wrong.append("should be cut off: \(p)") }
        let right = 60 - wrong.count
        XCTAssertEqual(Self.finished.count + Self.cutOff.count, 60)
        XCTAssertGreaterThanOrEqual(right, 55, "wrong ones: \(wrong)")
        print("EARLYEND fixture: \(right) of 60 right. Wrong: \(wrong)")
    }

    /// The costly mistake is a phrase that was still going judged finished. None of the 30 may be.
    func testNoCutOffPhraseIsEverJudgedFinished() {
        for p in Self.cutOff { XCTAssertFalse(VoiceIntent.looksFinished(p), p) }
    }

    func testEdgeCases() {
        XCTAssertFalse(VoiceIntent.looksFinished(""))
        XCTAssertFalse(VoiceIntent.looksFinished("   "))
        XCTAssertFalse(VoiceIntent.looksFinished("Stop."))             // one word never ends early
        XCTAssertFalse(VoiceIntent.looksFinished("Uh, um."))           // fillers only
        XCTAssertFalse(VoiceIntent.looksFinished("What time is it,"))  // a trailing comma
        XCTAssertFalse(VoiceIntent.looksFinished("Apples, oranges, bananas"))   // a list may go on
        XCTAssertTrue(VoiceIntent.looksFinished("what time is it"))    // no punctuation, question shape
        XCTAssertTrue(VoiceIntent.looksFinished("open my mail"))       // no punctuation, request shape
        XCTAssertFalse(VoiceIntent.looksFinished("open my"))
    }

    func testNeverTrueWhereLooksCutOffIsTrue() {
        for p in Self.finished + Self.cutOff where VoiceIntent.looksCutOff(p) {
            XCTAssertFalse(VoiceIntent.looksFinished(p), p)
        }
    }

    // MARK: Endpointer

    func testEarlyEndConstants() {
        XCTAssertGreaterThanOrEqual(Endpointer.earlyEndMs, Endpointer.earliestEarlyEndMs)
        XCTAssertGreaterThanOrEqual(Endpointer.earliestEarlyEndMs, 300)
        XCTAssertLessThan(Endpointer.earlyEndMs, Endpointer().endMs)
    }

    func testEndpointerCountsTheQuietRun() {
        var e = Endpointer()
        _ = e.feed(zeros(1.0))
        XCTAssertEqual(e.feed(tone(1.0)), [.started])
        XCTAssertEqual(e.quietMs, 0)
        _ = e.feed(zeros(0.4))
        XCTAssertGreaterThanOrEqual(e.quietMs, 340)
        XCTAssertLessThan(e.quietMs, 420)
        _ = e.feed(tone(0.1))
        XCTAssertEqual(e.quietMs, 0)                   // he spoke again: the quiet run starts over
    }

    // MARK: Ears, with a fake reader (no model, no sleeping)

    func testFinishedPhraseEndsAtEarlyQuietWithOneExtraRead() {
        let h = Harness(reads: ["What time is it?"])
        h.speak()
        h.quiet(0.40)
        h.drain()
        XCTAssertEqual(h.finals, ["What time is it?"])
        XCTAssertEqual(h.readCount, 1)                 // only the early read, no second final read
        h.quiet(0.60)                                  // the old 650 ms would have fired here: nothing more now
        h.drain()
        XCTAssertEqual(h.finals, ["What time is it?"])
        XCTAssertNotNil(h.ears.lastEarlyEnd)
    }

    func testCutOffPhraseWaitsForTheNormalEnd() {
        let h = Harness(reads: ["Tell me a paragraph about the"])
        h.speak()
        h.quiet(0.40)
        h.drain()
        XCTAssertEqual(h.finals, [])                   // not ended at 350 ms
        XCTAssertEqual(h.readCount, 1)                 // the one early read was made and refused
        XCTAssertNil(h.ears.lastEarlyEnd)
        h.quiet(0.40)                                  // past 650 ms of quiet in all
        h.drain()
        XCTAssertEqual(h.finals, ["Tell me a paragraph about the"])   // the normal read, as before
        XCTAssertEqual(h.readCount, 2)
    }

    func testOneWordNeverEndsEarly() {
        let h = Harness(reads: ["Yes."])
        h.speak()
        h.quiet(0.40)
        h.drain()
        XCTAssertEqual(h.finals, [])
        h.quiet(0.40)
        h.drain()
        XCTAssertEqual(h.finals, ["Yes."])
    }

    func testOnlyOneEarlyReadPerQuietRun() {
        let h = Harness(reads: ["Tell me about the"])
        h.speak()
        for _ in 0..<5 { h.quiet(0.06); h.drain() }    // 300 ms: not yet
        XCTAssertEqual(h.readCount, 0)
        for _ in 0..<3 { h.quiet(0.06); h.drain() }    // 480 ms: past 350, before 650
        XCTAssertEqual(h.readCount, 1)
    }

    func testClassModeKeepsItsOwnEnd() {
        let h = Harness(reads: ["What time is it?"])
        h.ears.setClassMode(true)
        h.speak()
        h.quiet(0.40)
        h.drain()
        XCTAssertEqual(h.readCount, 0)                 // no early read in class mode
        XCTAssertEqual(h.finals, [])
    }

    func testBackToCloseTalkTurnsEarlyEndBackOn() {
        let h = Harness(reads: ["What time is it?"])
        h.ears.setClassMode(true)
        h.ears.setClassMode(false)
        h.speak()
        h.quiet(0.40)
        h.drain()
        XCTAssertEqual(h.finals, ["What time is it?"])
    }

    func testSpeakingAgainAfterAnEarlyReadKeepsThePhrase() {
        // the early read says finished, but the quiet was not unbroken by the time the answer landed
        let h = Harness(reads: ["What time is it?"])
        h.holdReads()                                  // the read waits, so he can start talking again first
        h.speak()
        h.quiet(0.40)
        h.waitStarted()
        h.speak(0.3)                                   // he starts again before the read comes back
        _ = h.ears.inSpeech                            // lets the ears take those samples first
        h.releaseReads()
        h.drain()
        XCTAssertEqual(h.finals, [])                   // the early end is dropped, the phrase stays open
    }

    // MARK: merge counting

    func testEarlyMergeIsCountedOnlyWhenTheMergedMessageEndedEarly() {
        let t = Date()
        XCTAssertTrue(VoiceIntent.endedEarly(earlyAt: t, sentAt: t.addingTimeInterval(0.2)))
        XCTAssertFalse(VoiceIntent.endedEarly(earlyAt: nil, sentAt: t))
        XCTAssertFalse(VoiceIntent.endedEarly(earlyAt: t.addingTimeInterval(-10), sentAt: t))   // an old early end
    }

    // MARK: helpers

    func tone(_ s: Double) -> [Float] { (0..<Int(s * 16000)).map { 0.1 * sin(Float($0) * 0.12) } }
    func zeros(_ s: Double) -> [Float] { [Float](repeating: 0, count: Int(s * 16000)) }

    final class Harness: @unchecked Sendable {
        let ears = Ears()
        private let lock = NSLock()
        private var _finals: [String] = []
        private var _reads = 0
        private var script: [String]
        private let gate = DispatchSemaphore(value: 0)
        private let started = DispatchSemaphore(value: 0)
        private var holding = false
        var finals: [String] { lock.lock(); defer { lock.unlock() }; return _finals }
        var readCount: Int { lock.lock(); defer { lock.unlock() }; return _reads }

        init(reads: [String]) {
            script = reads
            ears.testReader = { [unowned self] _ in self.nextRead() }
            ears.onFrame = { [unowned self] f in
                if case .final(let t) = f { self.lock.lock(); self._finals.append(t); self.lock.unlock() }
            }
            ears.feed([Float](repeating: 0, count: 16000))     // a second of room first
        }
        private func nextRead() -> String {
            lock.lock(); _reads += 1
            let t = script.first ?? ""
            let hold = holding
            lock.unlock()
            if hold { started.signal(); gate.wait() }
            return t
        }
        func holdReads() { lock.lock(); holding = true; lock.unlock() }
        func waitStarted() { _ = started.wait(timeout: .now() + 5) }
        func releaseReads() { lock.lock(); holding = false; lock.unlock(); gate.signal() }
        func speak(_ s: Double = 1.0) { ears.feed((0..<Int(s * 16000)).map { 0.1 * sin(Float($0) * 0.12) }) }
        func quiet(_ s: Double) { ears.feed([Float](repeating: 0, count: Int(s * 16000))) }
        func drain() { ears._drainForTests() }
    }
}
