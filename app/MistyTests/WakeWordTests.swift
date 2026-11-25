import XCTest
@testable import MistyCore

/// The "Edith" wake word: when it may listen and fire (the gate), how it is tuned, the keyword tokens, how a wake-started turn
/// ends by itself, and the wiring from a detection to the same entry point the hotkey uses. No microphone and no model: a fake
/// mic and a fake spotter drive the real listener.
@MainActor
final class WakeWordTests: XCTestCase {

    // ---- the gate ---------------------------------------------------------------------------------------------------

    func testOpenGateAllowsListening() {
        XCTAssertTrue(WakeGate().open)
        XCTAssertNil(WakeGate().blocker)
    }

    func testEveryBlockerClosesTheGate() {
        let cases: [(String, (inout WakeGate) -> Void)] = [
            ("off", { $0.enabled = false }), ("paused", { $0.paused = true }), ("no model", { $0.modelReady = false }),
            ("no mic permission", { $0.micAllowed = false }), ("class", { $0.inClass = true }), ("call", { $0.inCall = true }),
            ("ambient", { $0.ambient = true }), ("dictating", { $0.dictating = true }), ("speaking", { $0.speaking = true }),
            ("turn", { $0.turnRunning = true }), ("voice busy", { $0.voiceIdle = false }),
        ]
        for (why, set) in cases {
            var g = WakeGate(); set(&g)
            XCTAssertFalse(g.open, why)
            XCTAssertEqual(g.blocker, why)
        }
    }

    func testVoiceStateFillsTheGate() {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        XCTAssertTrue(v.wakeGate(WakeGate()).open, "idle, silent, no class")
        v.testShow(state: .listening)
        XCTAssertEqual(v.wakeGate(WakeGate()).blocker, "voice busy")
        v.testShow(state: .thinking)
        XCTAssertFalse(v.wakeGate(WakeGate()).open)
        v.testShow(state: .speaking)
        XCTAssertFalse(v.wakeGate(WakeGate()).open, "never while she speaks")
        v.testShow(state: .idle)
        v.testStartAmbient(.name)
        v.testShow(state: .idle)
        XCTAssertEqual(v.wakeGate(WakeGate()).blocker, "ambient")
        v.testStartAmbient(.off)
        v.testShow(state: .idle)
        v.classMode = true
        XCTAssertEqual(v.wakeGate(WakeGate()).blocker, "class")
        v.classMode = false
        var base = WakeGate(); base.inClass = true
        XCTAssertEqual(v.wakeGate(base).blocker, "class", "a class the app knows about (ClassVM open) closes it too")
    }

    func testHerOwnLineAndItsEchoCloseTheGate() {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.updateMic = (open: { true }, close: {})
        v.speakReplies = true
        v.speakUpdate("Physics is at four.", toldID: "a")
        XCTAssertFalse(v.wakeGate(WakeGate()).open, "a line she began herself is playing")
        v.testLineFinished()
        XCTAssertEqual(v.wakeGate(WakeGate()).blocker, "speaking", "the room still rings with her voice for a moment")
    }

    // ---- tuning and keywords ----------------------------------------------------------------------------------------

    func testSensitivityMakesItEasierToFire() {
        let lo = WakeTuning.params(sensitivity: 0), mid = WakeTuning.params(sensitivity: 0.5), hi = WakeTuning.params(sensitivity: 1)
        XCTAssertGreaterThan(lo.threshold, mid.threshold); XCTAssertGreaterThan(mid.threshold, hi.threshold)
        XCTAssertLessThan(lo.score, mid.score); XCTAssertLessThan(mid.score, hi.score)
        XCTAssertEqual(WakeTuning.params(sensitivity: 9).threshold, hi.threshold, "clamped")
    }

    func testEdithHasKeywordTokensAndOtherWordsAreRefused() {
        let buf = WakeKeywords.buffer(for: "Edith")!
        XCTAssertTrue(buf.contains("▁E DI TH @EDITH\n"))
        XCTAssertEqual(buf.split(separator: "\n").count, 4)
        XCTAssertNil(WakeKeywords.buffer(for: "jarvis"))
        XCTAssertTrue(WakeSettings.standard.wordSupported)
        XCTAssertFalse(WakeSettings(word: "jarvis").wordSupported)
    }

    func testKeywordPiecesAreAllInTheModelsTokenList() throws {
        let tokens = WakeModel.dir(home: NSHomeDirectory() + "/Misty").appendingPathComponent("tokens.txt")
        guard let text = try? String(contentsOf: tokens, encoding: .utf8) else { throw XCTSkip("wake model not downloaded") }
        let known = Set(text.split(separator: "\n").compactMap { $0.split(separator: " ").first.map(String.init) })
        for w in ["edith", "misty"] {
            for line in WakeKeywords.lines(for: w)! { for piece in line.split(separator: " ") { XCTAssertTrue(known.contains(String(piece)), "\(w): \(piece)") } }
        }
    }

    func testSettingsDecodeFromTheEngine() throws {
        let s = try JSONDecoder().decode(WakeSettings.self, from: Data(#"{"enabled":false,"word":"edith","sensitivity":0.8}"#.utf8))
        XCTAssertEqual(s, WakeSettings(enabled: false, word: "edith", sensitivity: 0.8))
    }

    // ---- a wake-started turn ends by itself -------------------------------------------------------------------------

    private func tone(_ seconds: Double, amp: Float = 0.2) -> [Float] {
        (0..<Int(seconds * 16000)).map { amp * sin(Float($0) * 2 * .pi * 220 / 16000) }
    }
    private func quiet(_ seconds: Double) -> [Float] { [Float](repeating: 0.0004, count: Int(seconds * 16000)) }
    private func run(_ t: inout WakeTurn, _ audio: [Float]) -> [WakeTurn.Decision] {
        stride(from: 0, to: audio.count, by: 1600).map { t.feed(Array(audio[$0..<min($0 + 1600, audio.count)])) }.filter { $0 != .keep }
    }

    func testTurnSendsAfterHeSpeaksAndGoesQuiet() {
        var t = WakeTurn()
        XCTAssertEqual(run(&t, quiet(0.5) + tone(1.5) + quiet(2)), [.send])
    }

    func testTurnGivesUpWhenNothingIsSaid() {
        var t = WakeTurn()
        XCTAssertEqual(run(&t, quiet(8)), [.giveUp])
    }

    func testTheChimeAtTheStartIsNotHisSpeech() {
        var t = WakeTurn()
        XCTAssertEqual(run(&t, tone(0.25, amp: 0.3) + quiet(8)), [.giveUp], "a chime in the first moments must not count as him talking")
    }

    func testWordsAlreadySaidAfterEdithCountFromTheFirstSample() {
        var t = WakeTurn()                                   // "Edith, what's next today?" in one breath: speech is already going
        XCTAssertEqual(run(&t, quiet(0.35) + tone(2) + quiet(2)), [.send])
    }

    func testTurnDecidesOnlyOnce() {
        var t = WakeTurn()
        XCTAssertEqual(run(&t, quiet(0.5) + tone(1) + quiet(3) + tone(1) + quiet(3)).count, 1)
    }

    // ---- the activity gate and the tail ------------------------------------------------------------------------------

    func testSilenceIsInactiveAndSpeechOpensTheGateForAWhile() {
        var a = WakeActivity()
        XCTAssertFalse(a.feed(quiet(2)))
        XCTAssertTrue(a.feed(tone(0.4)), "sound opens it within a few frames")
        XCTAssertTrue(a.feed(quiet(1.0)), "and it stays open for the hangover, so a word said last is still decided")
        XCTAssertFalse(a.feed(quiet(1.0)), "then closes again")
    }

    func testAClickDoesNotOpenTheGate() {
        var a = WakeActivity()
        _ = a.feed(quiet(1))
        XCTAssertFalse(a.feed(tone(0.02, amp: 0.5) + quiet(0.5)))
    }

    func testASteadyHumStopsCounting() {
        var a = WakeActivity()
        _ = a.feed(quiet(1))
        var open = true
        for _ in 0..<40 { open = a.feed(tone(1, amp: 0.02)) }      // forty seconds of a fan-like hum
        XCTAssertFalse(open, "a steady sound becomes the floor")
    }

    func testTheSpotterOnlyHearsSoundAndStartsAFreshStreamEachTime() {
        let sp = FakeSpotter()
        let s = WakeStream(spotter: sp, gated: true) { }
        for _ in 0..<20 { s.feed(quiet(0.1)) }
        XCTAssertEqual(sp.fed, 0, "two seconds of silence cost nothing")
        s.feed(tone(0.3))
        XCTAssertGreaterThan(sp.fed, 16000 * 3 / 10, "it is given the sound and a little before it")
        XCTAssertEqual(sp.resets, 1)
        for _ in 0..<40 { s.feed(quiet(0.1)) }
        let fedAfter = sp.fed
        for _ in 0..<20 { s.feed(quiet(0.1)) }
        XCTAssertEqual(sp.fed, fedAfter, "and nothing again once the sound is long past")
        s.feed(tone(0.3))
        XCTAssertEqual(sp.resets, 2)
    }

    func testAnUtteranceStartIsASoundAfterAPause() {
        var a = WakeActivity()
        _ = a.feed(quiet(1))
        _ = a.feed(tone(2) + quiet(0.2) + tone(1))             // running speech: one start only
        XCTAssertEqual(a.onsets.count, 1)
        XCTAssertEqual(Double(a.onsets[0]) / 16000, 1.0, accuracy: 0.05)
        _ = a.feed(quiet(1) + tone(1))                         // a pause, then speech again
        XCTAssertEqual(a.onsets.count, 2)
    }

    func testTheNameAtTheStartOfAnUtteranceWakesAndTheSameWordMidSpeechDoesNot() {
        func run(wordAt seconds: Double) -> (hit: Bool, rejected: Int) {
            let sp = FakeSpotter(); sp.lag = 0.15
            let s = WakeStream(spotter: sp, gated: true) { }
            s.feed(quiet(2))
            var speech = tone(6)
            speech[Int(seconds * 16000)] = 0.95
            for i in stride(from: 0, to: speech.count, by: 1600) { s.feed(Array(speech[i..<min(i + 1600, speech.count)])) }
            return (s.hasFired, s.rejected)
        }
        XCTAssertTrue(run(wordAt: 0.6).hit, "called right after a pause")
        let late = run(wordAt: 4.0)
        XCTAssertFalse(late.hit, "said in the middle of running speech")
        XCTAssertEqual(late.rejected, 1)
    }

    func testTheTailStartsWhereTheWordEnded() {
        let sp = FakeSpotter()
        sp.endSeconds = 1.5                                   // the model reports it late: the word ended at 1.5 s of the 2 s fed
        var audio = (0..<32000).map { Float($0) / 100000 }    // 0.0 ... 0.32: each sample says where it is
        audio[31990] = 0.95
        let s = WakeStream(spotter: sp, gated: false) { }
        s.feed(audio)
        let t = s.takeTail()
        XCTAssertEqual(t.count, 8000, "the half second after the word is not lost")
        XCTAssertEqual(t.first, audio[24000])
    }

    // ---- the listener, with a fake mic and spotter ------------------------------------------------------------------

    final class FakeMic: WakeMic, @unchecked Sendable {
        var opened = 0, closed = 0
        var onSamples: (@Sendable ([Float]) -> Void)?
        func start(_ onSamples: @escaping @Sendable ([Float]) -> Void) throws { opened += 1; self.onSamples = onSamples }
        func stop() { closed += 1; onSamples = nil }
    }
    /// "Hears" the keyword whenever a sample of 0.9 goes by.
    final class FakeSpotter: WakeSpotter, @unchecked Sendable {
        var resets = 0, fed = 0
        var endSeconds: Double?
        var lag: Double?                                       // set: the word is reported this long after it ended
        func accept(_ samples: [Float]) -> WakeHit? {
            fed += samples.count
            guard samples.contains(where: { $0 > 0.9 }) else { return nil }
            return WakeHit(endSeconds: lag.map { max(0, Double(fed) / 16000 - $0) } ?? endSeconds)
        }
        func reset() { resets += 1 }
    }

    private func listener(gate: @escaping @MainActor () -> WakeGate, mic: FakeMic = FakeMic(), settings: WakeSettings = .standard) -> WakeListener {
        let l = WakeListener(home: "/nonexistent", settings: settings, mic: mic, make: { _, _ in FakeSpotter() })
        l.modelReady = { _ in true }
        l.gateAudio = false
        l.gate = gate
        return l
    }

    func testMicOpensWhenTheGateOpensAndClosesWhenItShuts() {
        let mic = FakeMic()
        var g = WakeGate()
        let l = listener(gate: { g }, mic: mic)
        l.evaluate()
        XCTAssertTrue(l.listening); XCTAssertEqual(mic.opened, 1)
        l.evaluate(); XCTAssertEqual(mic.opened, 1, "no reopen while it is open")
        g.speaking = true
        l.evaluate()
        XCTAssertFalse(l.listening); XCTAssertEqual(mic.closed, 1)
        XCTAssertEqual(l.lastBlocker, "speaking")
        g.speaking = false
        l.evaluate()
        XCTAssertTrue(l.listening); XCTAssertEqual(mic.opened, 2, "a fresh mic and spotter after the gate reopens")
    }

    func testSettingOffNeverOpensTheMic() {
        let mic = FakeMic()
        let l = listener(gate: { WakeGate() }, mic: mic, settings: WakeSettings(enabled: false))
        l.evaluate()
        XCTAssertFalse(l.listening); XCTAssertEqual(mic.opened, 0); XCTAssertEqual(l.lastBlocker, "off")
        l.apply(WakeSettings(enabled: true))
        XCTAssertTrue(l.listening)
        l.apply(WakeSettings(enabled: false))
        XCTAssertFalse(l.listening, "switching it off lets go of the mic at once")
    }

    func testMissingModelKeepsItOff() {
        let mic = FakeMic()
        let l = listener(gate: { WakeGate() }, mic: mic)
        l.modelReady = { _ in false }
        l.evaluate()
        XCTAssertEqual(mic.opened, 0); XCTAssertEqual(l.lastBlocker, "no model")
    }

    func testDetectionStartsTheSessionAndHandsOverWhatFollowed() async {
        let mic = FakeMic()
        var woke = 0
        let l = listener(gate: { WakeGate() }, mic: mic)
        l.onWake = { woke += 1; return true }
        l.evaluate()
        mic.onSamples?([0, 0.95, 0])                          // "Edith"
        mic.onSamples?([0.1, 0.2])                            // "what's next today"
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(woke, 1)
        mic.onSamples?([0.3])
        XCTAssertEqual(l.takeTail(), [0.1, 0.2, 0.3], "the words after the keyword are not lost in the hand-off")
        XCTAssertEqual(l.takeTail(), [])
        mic.onSamples?([0, 0.95, 0])
        try? await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(woke, 1, "one detection per open mic")
    }

    func testMicStaysOpenThroughTheHandoffThenLetsGo() async {
        let mic = FakeMic()
        var g = WakeGate()
        let l = listener(gate: { g }, mic: mic)
        l.onWake = { g.voiceIdle = false; return true }       // the session starts: the gate shuts
        l.evaluate()
        mic.onSamples?([0.95])
        try? await Task.sleep(for: .milliseconds(80))
        l.evaluate()
        XCTAssertTrue(l.listening, "the session has not taken the tail yet")
        _ = l.takeTail()
        l.evaluate()
        XCTAssertFalse(l.listening); XCTAssertEqual(mic.closed, 1)
    }

    func testDetectionWhileTheGateIsClosedIsDropped() async {
        let mic = FakeMic()
        var g = WakeGate()
        var woke = 0
        let l = listener(gate: { g }, mic: mic)
        l.onWake = { woke += 1; return true }
        l.evaluate()
        g.speaking = true                                      // her own voice says "Edith" before the tick closed the mic
        mic.onSamples?([0.95])
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(woke, 0)
    }

    func testARefusedWakeReArmsTheSpotter() async {
        let mic = FakeMic()
        let l = listener(gate: { WakeGate() }, mic: mic)
        l.onWake = { false }
        l.evaluate()
        mic.onSamples?([0.95])
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertTrue(l.listening)
        XCTAssertEqual(l.takeTail(), [])
    }

    // ---- the wiring to the hotkey flow ------------------------------------------------------------------------------

    func testWakeAndHotkeyReachTheSameStartTalk() async {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        var started: [Bool] = []
        var chimes = 0
        v.startTalkOverride = { started.append($0) }
        v.wakeChime = { chimes += 1 }
        let mic = FakeMic()
        let l = listener(gate: { WakeGate() }, mic: mic)
        WakeWiring.connect(listener: l, voice: v) { WakeGate() }
        await v.tap()
        XCTAssertEqual(started, [false], "the hotkey from idle")
        l.evaluate()
        mic.onSamples?([0.95])
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(started, [false, true], "the wake word: the same entry point")
        XCTAssertEqual(chimes, 1)
    }

    func testWakeIsRefusedWhileAVoiceTurnClassOrAmbientIsOn() async {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        var started = 0, chimes = 0
        v.startTalkOverride = { _ in started += 1 }
        v.wakeChime = { chimes += 1 }
        v.testShow(state: .thinking)
        var ok = await v.wake(); XCTAssertFalse(ok)
        v.testShow(state: .speaking)
        ok = await v.wake(); XCTAssertFalse(ok)
        v.testShow(state: .listening)
        ok = await v.wake(); XCTAssertFalse(ok)
        v.testShow(state: .idle)
        var base = WakeGate(); base.inClass = true
        ok = await v.wake(base: base); XCTAssertFalse(ok, "a class is recording")
        base = WakeGate(); base.enabled = false
        ok = await v.wake(base: base); XCTAssertFalse(ok, "the setting is off")
        v.testStartAmbient(.name); v.testShow(state: .idle)
        ok = await v.wake(); XCTAssertFalse(ok, "ambient mode owns her name")
        XCTAssertEqual(started, 0); XCTAssertEqual(chimes, 0)
        v.testStartAmbient(.off); v.testShow(state: .idle)
        ok = await v.wake(); XCTAssertTrue(ok)
        XCTAssertEqual(started, 1); XCTAssertEqual(chimes, 1)
    }
}
