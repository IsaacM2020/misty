import XCTest
@testable import MistyCore

final class ChordDetectorTests: XCTestCase {
    typealias M = ChordDetector.Mods
    let both = M(control: true, option: true)

    func testQuickTapFires() {
        var d = ChordDetector()
        XCTAssertFalse(d.flags(M(control: true), at: 0))
        XCTAssertFalse(d.flags(both, at: 0.05))
        XCTAssertFalse(d.flags(M(option: true), at: 0.2))      // one up, the other still down
        XCTAssertTrue(d.flags(M(), at: 0.25))
    }

    func testCommandOptionTapFiresToo() {
        var d = ChordDetector()
        XCTAssertFalse(d.flags(M(command: true), at: 0))
        XCTAssertFalse(d.flags(M(option: true, command: true), at: 0.05))
        XCTAssertTrue(d.flags(M(), at: 0.2))
    }

    func testRightHandKeyDoesNot() {
        var d = ChordDetector()
        _ = d.flags(M(option: true, command: true), at: 0)
        _ = d.flags(M(option: true, command: true, other: true), at: 0.1)
        XCTAssertFalse(d.flags(M(), at: 0.2))
    }

    func testHeldTooLongDoesNot() {
        var d = ChordDetector()
        _ = d.flags(both, at: 0)
        XCTAssertFalse(d.flags(M(), at: 0.9))
    }

    func testCtrlSpaceSiriDoesNot() {
        var d = ChordDetector()
        _ = d.flags(M(control: true), at: 0)
        d.key(at: 0.1)                                          // space
        _ = d.flags(both, at: 0.15)
        XCTAssertFalse(d.flags(M(), at: 0.3))
    }

    func testShortcutWithAKeyDoesNot() {
        var d = ChordDetector()
        _ = d.flags(both, at: 0)
        d.key(at: 0.1)                                          // ctrl+option+T
        XCTAssertFalse(d.flags(M(), at: 0.2))
    }

    func testOptionAloneWhisperDoesNot() {
        var d = ChordDetector()
        _ = d.flags(M(option: true), at: 0)
        XCTAssertFalse(d.flags(M(), at: 0.1))
    }

    func testCommandJoiningDoesNot() {
        var d = ChordDetector()
        _ = d.flags(both, at: 0)
        _ = d.flags(M(control: true, option: true, command: true), at: 0.1)
        XCTAssertFalse(d.flags(M(), at: 0.2))
    }

    func testWorksAgainAfterASpoiledOne() {
        var d = ChordDetector()
        _ = d.flags(both, at: 0); d.key(at: 0.1); _ = d.flags(M(), at: 0.2)
        _ = d.flags(both, at: 1.0)
        XCTAssertTrue(d.flags(M(), at: 1.2))
    }
}

final class VoiceIntentTests: XCTestCase {
    func testHushByeAndSay() {
        XCTAssertEqual(VoiceIntent.of("Stop.", confirming: false), .hush)
        XCTAssertEqual(VoiceIntent.of("shut up", confirming: false), .hush)
        XCTAssertEqual(VoiceIntent.of("Hold on.", confirming: false), .hush)
        XCTAssertEqual(VoiceIntent.of("Bye, Misty!", confirming: false), .bye)
        XCTAssertEqual(VoiceIntent.of("That's all.", confirming: false), .bye)
        XCTAssertEqual(VoiceIntent.of("Stop the timer at five", confirming: false), .say("Stop the timer at five"))
        XCTAssertEqual(VoiceIntent.of("Yes", confirming: false), .say("Yes"))
    }

    func testConfirmAnswers() {
        XCTAssertEqual(VoiceIntent.of("Yeah, do it.", confirming: true), .yes)
        XCTAssertEqual(VoiceIntent.of("No, don't.", confirming: true), .no)
        XCTAssertEqual(VoiceIntent.of("Actually send it to Mr Tan instead please", confirming: true),
                       .say("Actually send it to Mr Tan instead please"))
    }

    func testD3OnlyAClearYesIsAYes() {
        XCTAssertEqual(VoiceIntent.of("Yes", confirming: true), .yes)
        XCTAssertEqual(VoiceIntent.of("yes no", confirming: true), .say("yes no"))          // both words: not clear, so not a yes
        XCTAssertEqual(VoiceIntent.of("Hmm, maybe later", confirming: true), .say("Hmm, maybe later"))
        XCTAssertNotEqual(VoiceIntent.of("Hmm, maybe later", confirming: true), .yes)
    }

    func testRealSpeechVersusNoise() {
        XCTAssertTrue(VoiceIntent.isSpeech("wait"))
        XCTAssertFalse(VoiceIntent.isSpeech(""))
        XCTAssertFalse(VoiceIntent.isSpeech("a."))
    }

    func testFillersNeverCountAsSpeech() {
        XCTAssertFalse(VoiceIntent.isSpeech("Uh."))
        XCTAssertFalse(VoiceIntent.isSpeech("Hmm"))
        XCTAssertTrue(VoiceIntent.isSpeech("Um, wait"))
    }

    func testEchoOfHerOwnWords() {
        let said = ["Twenty-three times forty-seven is one thousand and eighty-one."]
        XCTAssertTrue(VoiceIntent.isEcho("forty-seven is one thousand", of: said))
        XCTAssertTrue(VoiceIntent.isEcho("Uh", of: said))
        XCTAssertFalse(VoiceIntent.isEcho("what about twelve squared", of: said))
    }

    func testOnlyAStopWordCutsHerOff() {
        let said = ["Octopuses have three hearts."]
        XCTAssertTrue(VoiceIntent.isBargeIn("Stop.", said: said))
        XCTAssertTrue(VoiceIntent.isBargeIn("hold on", said: said))
        XCTAssertTrue(VoiceIntent.isBargeIn("Misty, wait", said: said))
        XCTAssertTrue(VoiceIntent.isBargeIn("okay shut up", said: said))
        XCTAssertFalse(VoiceIntent.isBargeIn("But so there", said: said))       // her echo, turned into words
        XCTAssertFalse(VoiceIntent.isBargeIn("Any other one?", said: said))
        XCTAssertFalse(VoiceIntent.isBargeIn("three hearts", said: said))
    }

    func testRealWordsOverHerCutHerOff() {
        let said = ["Octopuses have three hearts."]
        XCTAssertTrue(VoiceIntent.isTalkOver("No, I mean", said: said))
        XCTAssertTrue(VoiceIntent.isTalkOver("no no no no", said: said))
        XCTAssertFalse(VoiceIntent.isTalkOver("Uh", said: said))
        XCTAssertFalse(VoiceIntent.isTalkOver("No", said: said))                 // one word is too little to go on
        XCTAssertFalse(VoiceIntent.isTalkOver("three hearts", said: said))       // her own words coming back
        XCTAssertTrue(VoiceIntent.looksCutOff("No, I mean"))                     // and then waits for the rest
    }

    func testHerOwnStopWordIsNotABargeIn() {
        XCTAssertFalse(VoiceIntent.isBargeIn("wait for it", said: ["Wait for it, octopuses have three hearts."]))
    }

    func testHerNameIsHeard() {
        XCTAssertTrue(VoiceIntent.namesHer("Misty, what's the time?"))
        XCTAssertTrue(VoiceIntent.namesHer("hey missy open notes"))
        XCTAssertFalse(VoiceIntent.namesHer("it's misting outside"))
    }

    func testASentenceCutOffMidThoughtWaits() {
        XCTAssertTrue(VoiceIntent.looksCutOff("Tell me a paragraph about the"))
        XCTAssertTrue(VoiceIntent.looksCutOff("can you open my notes and"))
        XCTAssertTrue(VoiceIntent.looksCutOff("I want something like, uh"))
        XCTAssertFalse(VoiceIntent.looksCutOff("Generate a paragraph about Rubik's cubes."))
        XCTAssertFalse(VoiceIntent.looksCutOff("what is this about?"))
    }

    func testClassModeByVoice() {
        XCTAssertEqual(VoiceIntent.classMode("Misty, class mode."), true)
        XCTAssertEqual(VoiceIntent.classMode("Misty class mode off"), false)
        XCTAssertNil(VoiceIntent.classMode("I have class at nine"))      // no name: just talk
    }

    func testLoneAckIsRoomNoise() {
        XCTAssertTrue(VoiceIntent.isLoneAck("Yeah."))
        XCTAssertTrue(VoiceIntent.isLoneAck("Sure."))
        XCTAssertFalse(VoiceIntent.isLoneAck("Yeah, open my English work"))
    }
}

final class EndpointerTests: XCTestCase {
    static let rate = Endpointer.rate
    func tone(_ seconds: Double, _ amp: Float) -> [Float] {
        (0..<Int(seconds * Double(Self.rate))).map { amp * sin(Float($0) * 0.12) }
    }
    func noise(_ seconds: Double, _ amp: Float) -> [Float] {
        (0..<Int(seconds * Double(Self.rate))).map { _ in Float.random(in: -amp...amp) }
    }

    func testPhraseStartsAndEndsAfterQuiet() {
        var e = Endpointer()
        XCTAssertEqual(e.feed(noise(1.0, 0.001)), [])
        let start = e.feed(tone(1.0, 0.1))
        XCTAssertEqual(start, [.started])
        let end = e.feed(noise(1.0, 0.001))
        guard case .ended(let phrase)? = end.first else { return XCTFail("no end: \(end)") }
        XCTAssertGreaterThan(phrase.count, Self.rate)            // the speech plus preroll and the quiet tail
    }

    /// A teacher talking for 20 s without a pause: the phrase closes below the ears' 15 s window (the model pads a
    /// 15.0 s phrase to 15.02 s and refuses it), so nothing is lost.
    func testNonstopSpeechClosesUnderTheEncoderWindow() {
        var e = Endpointer()
        e.classTuned()
        _ = e.feed(noise(1.0, 0.001))
        let out = e.feed(tone(20.0, 0.1))
        guard case .ended(let phrase)? = out.first(where: { if case .ended = $0 { return true }; return false })
        else { return XCTFail("no end: \(out)") }
        XCTAssertLessThanOrEqual(phrase.count, Int(Endpointer.maxPhraseSeconds * Double(Self.rate)))
        XCTAssertLessThan(Endpointer.maxPhraseSeconds, 15.0)
    }

    /// In a class, a long stretch of talk closes at the first short pause after 6 s, so the ears read shorter
    /// chunks (they drop words in 14 s ones) without cutting through a word.
    func testClassPhraseClosesAtAPauseAfterSixSeconds() {
        var e = Endpointer()
        e.classTuned()
        _ = e.feed(noise(1.0, 0.001))
        XCTAssertEqual(e.feed(tone(4.0, 0.1) + noise(0.15, 0.001) + tone(3.0, 0.1)), [.started])   // a pause before 6 s: one phrase
        let out = e.feed(noise(0.15, 0.001))
        guard case .ended(let phrase)? = out.first else { return XCTFail("no end at the pause: \(out)") }
        XCTAssertGreaterThan(phrase.count, 7 * Self.rate)
        XCTAssertLessThan(phrase.count, 9 * Self.rate)
    }

    func testCloseTalkKeepsItsLongerPause() {
        var e = Endpointer()
        _ = e.feed(noise(1.0, 0.001))
        XCTAssertEqual(e.feed(tone(7.0, 0.1) + noise(0.15, 0.001)), [.started])          // close talk: 0.15 s is not the end
    }

    func testShortClickIsNotSpeech() {
        var e = Endpointer()
        _ = e.feed(noise(1.0, 0.001))
        XCTAssertEqual(e.feed(tone(0.06, 0.2) + noise(0.5, 0.001)), [])
    }

    func testEndsInANoisyRoomRelativeToHisVoice() {
        var e = Endpointer()
        _ = e.feed(noise(1.0, 0.0005))                          // quiet room, then a fan starts mid-phrase
        XCTAssertEqual(e.feed(tone(1.0, 0.1)), [.started])
        let end = e.feed(noise(1.5, 0.008))
        guard case .ended? = end.first else { return XCTFail("the phrase should close: the fan is a tenth of his voice") }
    }

    func testSteadyNoiseBecomesTheFloor() {
        var e = Endpointer()
        _ = e.feed(noise(0.5, 0.0005))
        XCTAssertEqual(e.feed(noise(1.0, 0.02)), [.started])     // a fan switches on: looks like speech at first
        e.dropAsNoise()                                         // Ears saw no words in it
        XCTAssertEqual(e.feed(noise(2.0, 0.02)), [], "the fan is the floor now")
        XCTAssertEqual(e.feed(tone(0.5, 0.2)), [.started])
    }

    func testSpeakingBarKeepsEchoOut() {
        var e = Endpointer()
        _ = e.feed(noise(1.0, 0.0005))
        e.minLevel = Endpointer.speakingMinLevel
        XCTAssertEqual(e.feed(tone(1.0, 0.005)), [])             // her echo, quieter than the bar
        XCTAssertEqual(e.feed(tone(0.5, 0.1)), [.started])        // Isaac up close
    }

    func testWhileSheTalksOnlyClearlyLouderThanHerEchoStarts() {
        var e = Endpointer()
        _ = e.feed(noise(0.5, 0.0005))
        e.minLevel = Endpointer.speakingMinLevel
        e.overEcho = true
        _ = e.feed(tone(1.5, 0.05))                              // her echo opens a phrase at her first word...
        e.reset()                                               // ...which Ears drops
        XCTAssertEqual(e.feed(tone(1.0, 0.05)), [], "her echo, now the bar")
        XCTAssertEqual(e.feed(tone(0.5, 0.2)), [.started], "Isaac clearly louder")
    }

    func testForceEndHandsBackThePhrase() {
        var e = Endpointer()
        _ = e.feed(noise(0.5, 0.0005))
        _ = e.feed(tone(0.5, 0.1))
        XCTAssertNotNil(e.forceEnd())
        XCTAssertFalse(e.inSpeech)
        XCTAssertNil(e.forceEnd())
    }
}

final class SamplePhraseTests: XCTestCase {
    func testTestPhraseNeverPlaysDuringAClass() {
        XCTAssertTrue(VoiceVM.maySpeakSample(classMode: false, classOpen: false))
        XCTAssertFalse(VoiceVM.maySpeakSample(classMode: true, classOpen: false))      // silent mode
        XCTAssertFalse(VoiceVM.maySpeakSample(classMode: false, classOpen: true))      // class session open
        XCTAssertFalse(VoiceVM.maySpeakSample(classMode: true, classOpen: true))
    }
}

final class TurnNotOverTests: XCTestCase {
    /// "One sec." finishes while the calendar tool still runs: the app must keep the turn open for the real answer.
    func testOneSecIsNotTheWholeAnswer() {
        XCTAssertTrue(VoiceVM.moreComing(talking: true, replyDone: false, asking: false))
    }
    func testTurnEndsOnceTheReplyIsDone() {
        XCTAssertFalse(VoiceVM.moreComing(talking: true, replyDone: true, asking: false))
    }
    func testHerYesNoQuestionWaitsForHim() {
        XCTAssertFalse(VoiceVM.moreComing(talking: true, replyDone: false, asking: true))
    }
    func testLiveModeIsUntouched() {
        XCTAssertFalse(VoiceVM.moreComing(talking: false, replyDone: false, asking: false))
    }
}
