import XCTest
@testable import MistyCore

/// F4: talk over a line she began herself. Fake frames into the voice code and a fake mic: no sound, no model.
@MainActor
final class UpdateTalkOverTests: XCTestCase {
    final class Mic: @unchecked Sendable {
        var opened = 0, closed = 0, allow = true
        var isOpen: Bool { opened > closed }
    }

    let line = "Your cricket match starts in twenty minutes, and the pitch report looks dry."

    func make() -> (VoiceVM, Mic, FakeAPI) {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.speakReplies = true
        let m = Mic()
        v.updateMic = (open: { if m.allow { m.opened += 1 }; return m.allow }, close: { m.closed += 1 })
        return (v, m, api)
    }

    func waitFor(_ api: FakeAPI, _ method: String) async -> Bool {
        let end = Date().addingTimeInterval(2)
        while !api.calls.contains(where: { $0.method == method }) && Date() < end { try? await Task.sleep(for: .milliseconds(5)) }
        return api.calls.contains { $0.method == method }
    }

    // ---- the mic is open for the length of the line and released after ---------------------------------------------
    func testALineOpensTheMicAndSaysSheIsSpeaking() {
        let (v, m, _) = make()
        v.speakUpdate(line, toldID: "td_1")
        XCTAssertTrue(m.isOpen)
        XCTAssertTrue(v.updateLineOpen)
        XCTAssertEqual(v.state, .speaking)
        XCTAssertEqual(v.presenceVoice, "speaking")
    }

    func testALineThatEndsClosesTheInputAndGoesBackToIdle() {
        let (v, m, api) = make()
        v.speakUpdate(line, toldID: "td_1")
        v.testLineFinished()
        XCTAssertFalse(m.isOpen)
        XCTAssertEqual(m.opened, 1)
        XCTAssertEqual(m.closed, 1)
        XCTAssertFalse(v.updateLineOpen)
        XCTAssertEqual(v.state, .idle)
        XCTAssertEqual(v.presenceVoice, "idle")
        XCTAssertTrue(api.calls.filter { $0.method == "told.interrupted" }.isEmpty)       // a line he lets finish costs nothing
    }

    func testTwoLinesInARowEachOpenAndCloseOnce() {
        let (v, m, _) = make()
        v.speakUpdate(line, toldID: "a"); v.testLineFinished()
        v.speakUpdate("Second line about the weather.", toldID: "b"); v.testLineFinished()
        XCTAssertEqual(m.opened, 2)
        XCTAssertEqual(m.closed, 2)
        XCTAssertFalse(m.isOpen)
    }

    func testAMicThatWillNotOpenMeansTheLineIsSaidAsBefore() {
        let (v, m, _) = make()
        m.allow = false
        v.speakUpdate(line, toldID: "td_1")
        XCTAssertFalse(v.updateLineOpen)
        XCTAssertEqual(v.state, .idle)
        XCTAssertEqual(m.opened, 0)
    }

    func testClassOpenMeansNoMicAndNoLine() {
        let (v, m, _) = make()
        v.classMode = true
        v.speakUpdate(line, toldID: "td_1")
        XCTAssertEqual(m.opened, 0)
        XCTAssertFalse(v.updateLineOpen)
        XCTAssertEqual(v.state, .idle)
    }

    func testMutedMeansNoMic() {
        let (v, m, _) = make()
        v.speakReplies = false
        v.speakUpdate(line, toldID: "td_1")
        XCTAssertEqual(m.opened, 0)
        XCTAssertEqual(v.state, .idle)
    }

    func testALineWhileAConversationIsGoingDoesNotOpenAnotherMic() {
        let (v, m, _) = make()
        v.speakUpdate(line, toldID: "a")
        v.speakUpdate("Another line.", toldID: "b")          // one is already running
        XCTAssertEqual(m.opened, 1)
    }

    func testAClassOpeningMidLineClosesTheMic() {
        let (v, m, _) = make()
        v.speakUpdate(line, toldID: "a")
        v.classMode = true
        XCTAssertFalse(m.isOpen)
        XCTAssertEqual(v.state, .idle)
    }

    func testTheKeyCutsTheLineShortAndReleasesTheMic() async {
        let (v, m, _) = make()
        v.speakUpdate(line, toldID: "a")
        var lines: [String] = []
        VoiceLog.sink = { lines.append($0) }
        defer { VoiceLog.sink = nil }
        await v.tap()
        XCTAssertEqual(v.state, .idle)
        XCTAssertFalse(v.updateLineOpen)
        XCTAssertTrue(lines.contains { $0.contains("UPDATE_INTERRUPT") })
        XCTAssertGreaterThanOrEqual(m.closed, 1)
    }

    // ---- talking over it ---------------------------------------------------------------------------------------------
    func testTwoRealWordsOverTheLineStopHerAndBecomeATurn() async {
        let (v, m, api) = make()
        v.speakUpdate(line, toldID: "td_9")
        v.testHeard(.partial("actually wait"))
        XCTAssertEqual(v.state, .listening)                  // silent, listening again
        XCTAssertFalse(v.updateLineOpen)
        XCTAssertTrue(m.isOpen)                              // the session goes on for his turn
        let ok = await waitFor(api, "told.interrupted")
        XCTAssertTrue(ok)
        XCTAssertTrue(api.calls.first { $0.method == "told.interrupted" }!.params.contains("td_9"))
        v.testHeard(.final("actually wait what time is the match"))
        XCTAssertEqual(v.state, .thinking)                   // a normal turn: the follow-up window took it
        let sent = await waitFor(api, "chat.send")
        XCTAssertTrue(sent)
        v.end()
        XCTAssertFalse(m.isOpen)
    }

    func testAStopWordAloneStopsHer() {
        let (v, _, _) = make()
        v.speakUpdate(line, toldID: "a")
        v.testHeard(.partial("stop"))
        XCTAssertEqual(v.state, .listening)
    }

    func testOneWordMumbleDoesNotStopHer() {
        let (v, _, _) = make()
        v.speakUpdate(line, toldID: "a")
        v.testHeard(.partial("hmm"))
        XCTAssertEqual(v.state, .speaking)
        XCTAssertTrue(v.updateLineOpen)
    }

    func testTheTalkOverIsLoggedWithSourceUpdate() {
        var lines: [String] = []
        VoiceLog.sink = { lines.append($0) }
        defer { VoiceLog.sink = nil }
        let (v, _, _) = make()
        v.speakUpdate(line, toldID: "a")
        v.testHeard(.partial("hold on please"))
        XCTAssertTrue(lines.contains { $0.contains("barge_stop_ms=") && $0.contains("source=update") })
    }

    func testNoToldIDMeansNoEngineCallButStillStops() async {
        let (v, _, api) = make()
        v.speakUpdate(line)
        v.testHeard(.partial("hold on please"))
        XCTAssertEqual(v.state, .listening)
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertTrue(api.calls.filter { $0.method == "told.interrupted" }.isEmpty)
    }

    // ---- echo ----------------------------------------------------------------------------------------------------------
    func testHerOwnLineHeardBackNeverStopsHer() {
        let (v, _, _) = make()
        v.speakUpdate(line, toldID: "a")
        v.testHeard(.partial("your cricket match starts in twenty minutes"))
        v.testHeard(.partial("your cricket match starts in twenty minutes and the pitch report"))
        XCTAssertEqual(v.state, .speaking)
        v.testHeard(.final("your cricket match starts in twenty minutes and the pitch report looks dry"))
        XCTAssertEqual(v.state, .speaking)
        XCTAssertTrue(v.updateLineOpen)
    }

    func testHerOwnStopWordInTheLineIsNotAnInterrupt() {
        let (v, _, _) = make()
        v.speakUpdate("Please stop by the shop before it closes tonight, they hold your order.", toldID: "a")
        v.testHeard(.partial("please stop by the shop"))
        XCTAssertEqual(v.state, .speaking)
    }

    func testEchoAtTheMeasuredLevelDoesNotStartAPhrase() {
        // her echo reaches the mic at about 0.002 RMS; with the speaking bar up, only a clearly louder voice starts a phrase
        var e = Endpointer()
        _ = e.feed(Self.noise(0.5, 0.0005))
        e.minLevel = Endpointer.speakingMinLevel
        e.overEcho = true
        XCTAssertEqual(e.feed(Self.tone(1.5, 0.002)), [])
        XCTAssertEqual(e.feed(Self.tone(0.5, 0.2)), [.started])
    }

    static func tone(_ sec: Double, _ amp: Float) -> [Float] {
        (0..<Int(sec * 16000)).map { amp * sin(Float($0) * 0.05) }
    }
    static func noise(_ sec: Double, _ amp: Float) -> [Float] {
        (0..<Int(sec * 16000)).map { _ in Float.random(in: -amp...amp) }
    }

    // ---- other turns are untouched --------------------------------------------------------------------------------------
    func testNothingSpokenOnItsOwnMeansNoMic() {
        let (v, m, _) = make()
        XCTAssertEqual(m.opened, 0)
        XCTAssertEqual(v.state, .idle)
    }
}
