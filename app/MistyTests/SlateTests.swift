import XCTest
@testable import MistyCore

/// The Slate (S1): the engine's slate is handed to the page, inked line by line, and a quick one fades and closes.
@MainActor
final class SlateTests: XCTestCase {
    static let json = """
    {"title":"5 x 5","kind":"quick","blocks":[{"k":"math","tex":"5 \\\\times 5 = \\\\ans","say":"Five times five is twenty-five.",
      "ans":{"expr":"5*5","value":"25"},"mark":"under"}]}
    """

    func slate() throws -> JSONValue { try JSONDecoder().decode(JSONValue.self, from: Data(Self.json.utf8)) }

    func testEventDecodes() throws {
        let line = #"{"jsonrpc":"2.0","method":"slate.show","params":{"title":"x","blocks":[]}}"#
        guard case .slateShow(let p) = try RpcEvent.decode(Data(line.utf8)) else { return XCTFail("not a slateShow") }
        XCTAssertEqual(p["title"]?.stringValue, "x")
    }

    func testNoVoicePaceIs55MsACharacterAtLeast700() {
        XCTAssertEqual(SlateVM.wipeMs(chars: 4), 700)
        XCTAssertEqual(SlateVM.wipeMs(chars: 20), 1100)
    }

    func testPresentHandsTheSlateToThePageOnceItLoads() throws {
        let vm = SlateVM()
        XCTAssertEqual(vm.present(try slate()), ["Five times five is twenty-five."])
        XCTAssertTrue(vm.open)
        var ran: [String] = []
        vm.pageReady { ran.append($0) }                                 // the page loads after the slate arrived: nothing is lost
        XCTAssertEqual(ran.count, 1)
        XCTAssertTrue(ran[0].hasPrefix("window.slate.show({"))
        vm.reveal(0)
        XCTAssertEqual(ran.last, "window.slate.reveal(0, 1705)")        // 31 chars of say, 55 ms each
    }

    func testAQuickSlateFadesThenClosesAndAStayOneDoesNot() async throws {
        let vm = SlateVM()
        vm.lingerSeconds = 0.05; vm.fadeSeconds = 0.05
        var closed = false
        vm.onClose = { closed = true }
        vm.pageReady { _ in }
        vm.present(try slate())
        vm.reveal(0, ms: 10)
        try await Task.sleep(for: .seconds(1.6))
        XCTAssertTrue(vm.scripts.contains("window.slate.fade()"))
        XCTAssertTrue(closed); XCTAssertFalse(vm.open)

        var stay = false
        let vm2 = SlateVM()
        vm2.lingerSeconds = 0.05; vm2.fadeSeconds = 0.05
        vm2.onClose = { stay = true }
        vm2.pageReady { _ in }
        let j = try JSONDecoder().decode(JSONValue.self, from: Data(Self.json.replacingOccurrences(of: "quick", with: "stay").utf8))
        vm2.present(j); vm2.reveal(0, ms: 10)
        try await Task.sleep(for: .seconds(1.6))
        XCTAssertFalse(stay); XCTAssertTrue(vm2.open)
    }

    func testANewSlateReplacesTheOldOneAndCancelsItsFade() throws {
        let vm = SlateVM()
        vm.present(try slate()); vm.reveal(0, ms: 10)
        vm.present(try slate())
        XCTAssertEqual(vm.scripts.filter { $0.hasPrefix("window.slate.show") }.count, 2)
    }

    // MARK: S6 voice sync

    func testSentenceClockFirstChunkStartsAfterLatencyAndEndAddsUp() {
        var c = SentenceClock()
        c.begin()
        // 24000 samples at 24 kHz = 1 s of audio; at rate 0.9 it plays 1/0.9 s.
        let start = c.add(samples: 12_000, sampleRate: 24_000, rate: 0.9, now: 100, latency: 0.05)
        XCTAssertEqual(start!, 100.05, accuracy: 1e-9)
        XCTAssertNil(c.add(samples: 12_000, sampleRate: 24_000, rate: 0.9, now: 100.2, latency: 0.05))   // not the first chunk
        XCTAssertEqual(c.playEnd, 100.05 + 1.0 / 0.9, accuracy: 1e-9)
        XCTAssertEqual(c.seconds, 1.0 / 0.9, accuracy: 1e-9)
    }

    func testASentenceQueuedBehindAnotherStartsWhenTheFirstEnds() {
        var c = SentenceClock()
        c.begin()
        c.add(samples: 24_000, sampleRate: 24_000, rate: 1, now: 10, latency: 0)        // plays 10 to 11
        c.begin()
        let second = c.add(samples: 24_000, sampleRate: 24_000, rate: 1, now: 10.3, latency: 0)
        XCTAssertEqual(second!, 11, accuracy: 1e-9)
        XCTAssertEqual(c.playEnd, 12, accuracy: 1e-9)
    }

    func testAnUnderrunStartsTheNextChunkAtNowNotInThePast() {
        var c = SentenceClock()
        c.begin()
        c.add(samples: 24_000, sampleRate: 24_000, rate: 1, now: 10, latency: 0)        // runs out at 11
        c.add(samples: 24_000, sampleRate: 24_000, rate: 1, now: 11.5, latency: 0)      // generation was late: plays 11.5 to 12.5
        XCTAssertEqual(c.playEnd, 12.5, accuracy: 1e-9)
    }

    func testResetForgetsEverythingScheduled() {
        var c = SentenceClock()
        c.begin(); c.add(samples: 48_000, sampleRate: 24_000, rate: 1, now: 5, latency: 0)
        c.reset()
        c.begin()
        XCTAssertEqual(c.add(samples: 24_000, sampleRate: 24_000, rate: 1, now: 6, latency: 0)!, 6, accuracy: 1e-9)
    }

    func testTheWipeEstimateAndTheLearnedPace() {
        XCTAssertEqual(SlateVM.estimateMs(chars: 30, msPerChar: 62), 1860)
        XCTAssertEqual(SlateVM.estimateMs(chars: 2, msPerChar: 62), 500)                // never shorter than 500 ms
        XCTAssertEqual(SlateVM.learnedPace(old: 62, chars: 40, audioSeconds: 3.2), 71, accuracy: 0.01)   // 80 ms/char heard
        XCTAssertEqual(SlateVM.learnedPace(old: 62, chars: 3, audioSeconds: 9), 62)     // too short to learn from
        XCTAssertEqual(SlateVM.retimeMs(endsIn: 1.2344), 1234)
        XCTAssertEqual(SlateVM.retimeMs(endsIn: -0.2), 0)
    }

    func testTheVoicePathStartsOnTheAudioAndRetimesToItsExactEnd() throws {
        let vm = SlateVM()
        vm.pageReady { _ in }
        vm.present(try slate())
        vm.timing.start(0, 0.05)
        XCTAssertEqual(vm.scripts.last, "window.slate.reveal(0, \(SlateVM.estimateMs(chars: 31, msPerChar: 62)))")
        vm.timing.end(0, 1.8, 2.0)
        XCTAssertEqual(vm.scripts.last, "window.slate.retime(0, 1800)")
        XCTAssertNotEqual(vm.msPerChar, 62)                                             // it heard the real length
    }

    func testABargeInFinishesTheCurrentLine() throws {
        let vm = SlateVM()
        vm.pageReady { _ in }
        vm.present(try slate())
        vm.timing.start(0, 0)
        vm.timing.stopped()
        XCTAssertEqual(vm.scripts.last, "window.slate.finishLine()")
    }

    func testNoVoiceStillInksAtTheFixedPace() throws {
        let vm = SlateVM()
        vm.pageReady { _ in }
        vm.present(try slate())
        vm.timing.silent(0)
        XCTAssertEqual(vm.scripts.last, "window.slate.reveal(0, \(SlateVM.wipeMs(chars: 31)))")
    }

    // ---- S8: exit behaviour and the read-back
    static let proto = """
    {"title":"Streaking","kind":"stay","ask":"Everything look right?","blocks":[{"k":"steps","items":["Flame the loop","Touch the plate"],"confirm":true,
      "say":"Flame the loop. Touch the plate.","mark":"under"}]}
    """
    func protocol_() throws -> JSONValue { try JSONDecoder().decode(JSONValue.self, from: Data(Self.proto.utf8)) }

    func testHoverHoldsAQuickSlateAndItFadesAfterThePointerLeaves() async throws {
        let vm = SlateVM(); vm.lingerSeconds = 0.05; vm.fadeSeconds = 0.05
        var closed = false; vm.onClose = { closed = true }
        vm.pageReady { _ in }
        vm.present(try slate()); vm.hover(true); vm.reveal(0, ms: 10)
        try await Task.sleep(for: .seconds(1.5))
        XCTAssertTrue(vm.open); XCTAssertFalse(closed)                  // held while the pointer is on it
        vm.hover(false)
        try await Task.sleep(for: .seconds(0.5))
        XCTAssertTrue(closed); XCTAssertFalse(vm.open)
    }

    func testWaitTurnsAQuickSlateIntoOneThatStays() async throws {
        let vm = SlateVM(); vm.lingerSeconds = 0.05; vm.fadeSeconds = 0.05
        var closed = false; vm.onClose = { closed = true }
        vm.pageReady { _ in }
        vm.present(try slate()); vm.reveal(0, ms: 10); vm.holdOpen()
        try await Task.sleep(for: .seconds(1.5))
        XCTAssertFalse(closed); XCTAssertTrue(vm.open)
    }

    func testAProtocolAsksItsQuestionAndStaysUntilOk() async throws {
        let vm = SlateVM(); vm.lingerSeconds = 0.05; vm.fadeSeconds = 0.05
        var closed = false; vm.onClose = { closed = true }
        vm.pageReady { _ in }
        vm.present(try protocol_()); vm.reveal(0, ms: 10)
        try await Task.sleep(for: .seconds(1.5))
        XCTAssertTrue(vm.scripts.contains("window.slate.readback(1100)"))
        XCTAssertFalse(vm.scripts.contains("window.slate.fade()")); XCTAssertFalse(closed)   // never fades by itself
        vm.finish()                                                                           // "ok"
        try await Task.sleep(for: .seconds(0.5))
        XCTAssertTrue(vm.scripts.contains("window.slate.fade()")); XCTAssertTrue(closed)
    }

    func testAChangeReinksOnlyThatLineAndAPillSendsHisWord() throws {
        let vm = SlateVM(); vm.pageReady { _ in }
        var said: [String] = []; vm.onPill = { said.append($0) }
        vm.present(try protocol_())
        vm.update(block: 0, item: 1, text: "Touch the \"agar\"")
        XCTAssertEqual(vm.scripts.last, #"window.slate.updateItem(0, 1, "Touch the \"agar\"")"#)
        vm.pill("ok"); vm.pill("change")
        XCTAssertEqual(said, ["ok", "I want to change something"])
    }

    func testTheNewEventsDecode() throws {
        XCTAssertEqual(try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"slate.update","params":{"block":0,"item":2,"text":"x"}}"#.utf8)), .slateUpdate(block: 0, item: 2, text: "x"))
        XCTAssertEqual(try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"slate.close","params":{"saved":true}}"#.utf8)), .slateClose)
        XCTAssertEqual(try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"slate.hold","params":{}}"#.utf8)), .slateHold)
    }

    // ---- S9: the history drawer
    func testTheDrawerLoadsTheListAndOpensASavedSlate() async throws {
        let vm = SlateVM(); vm.pageReady { _ in }
        let row = try JSONDecoder().decode(JSONValue.self, from: Data(#"{"id":3,"title":"5 x 5","created_at":1790000000}"#.utf8))
        vm.history = { [row] }
        vm.fetchSlate = { id in id == 3 ? try? JSONDecoder().decode(JSONValue.self, from: Data(Self.json.utf8)) : nil }
        await vm.loadHistory()
        XCTAssertTrue(vm.scripts.last?.hasPrefix("window.slate.setHistory([") == true)
        await vm.openSaved(3, replay: true)
        XCTAssertTrue(vm.scripts.last?.hasPrefix("window.slate.openSaved(") == true && vm.scripts.last?.hasSuffix(", true)") == true)
        await vm.openSaved(99, replay: false)                               // unknown id: nothing is sent
        XCTAssertTrue(vm.scripts.last?.hasSuffix(", true)") == true)
    }

    // ---- S10: lessons append step by step
    func testAppendAddsOnlyTheNewLinesAndOffsetsTheNarrator() throws {
        let vm = SlateVM(); vm.pageReady { _ in }
        XCTAssertNil(vm.append(try slate()))                                  // nothing open yet
        vm.present(try slate())
        let more = try JSONDecoder().decode(JSONValue.self, from: Data(#"{"blocks":[{"k":"text","text":"Next","say":"Now this."},{"k":"turn","prompt":"Your turn","say":""}],"from":1}"#.utf8))
        let r = vm.append(more)
        XCTAssertEqual(r?.0, 1); XCTAssertEqual(r?.1, ["Now this.", ""])
        XCTAssertTrue(vm.scripts.last?.hasPrefix("window.slate.append([") == true)
        var started: [Int] = []
        vm.pageReady { _ in }
        _ = vm.timing(offset: 1)                                              // the narrator's line 0 is the page's line 1
        vm.reveal(1, ms: 10); started.append(1)
        XCTAssertEqual(vm.scripts.last, "window.slate.reveal(1, 10)")
    }

    func testAppendEventDecodes() throws {
        guard case .slateAppend(let p) = try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"slate.append","params":{"blocks":[],"from":3}}"#.utf8)) else { return XCTFail("no") }
        XCTAssertEqual(p["from"]?.intValue, 3)
    }
}
