import XCTest
@testable import MistyCore

final class ProactiveTests: XCTestCase {
    func testSheSpeaksUpEventDecodes() throws {
        let json = #"{"jsonrpc":"2.0","method":"proactive.say","params":{"id":"td_1","how":"ask","text":"I have an update on the match. Want to hear it?","urgency":"soon","source_name":null,"url":null}}"#
        let ev = try RpcEvent.decode(Data(json.utf8))
        XCTAssertEqual(ev, .proactive(id: "td_1", how: "ask", text: "I have an update on the match. Want to hear it?"))
    }

    func testAnEventWithoutTextIsIgnoredNotACrash() throws {
        let json = #"{"jsonrpc":"2.0","method":"proactive.say","params":{"id":"td_2"}}"#
        XCTAssertEqual(try RpcEvent.decode(Data(json.utf8)), .unknown("proactive.say"))
    }

    @MainActor
    func testIdleMeansIdleForTheGate() {
        let v = VoiceVM(api: FakeAPI(), chat: ChatVM(api: FakeAPI()))
        XCTAssertEqual(v.presenceVoice, "idle")
    }

    // F1: the key press asks the engine to warm the cache, fire and forget.
    @MainActor
    func testWarmCacheCallsTheEngineOnceAndDoesNotWait() async {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.warmCache()
        let end = Date().addingTimeInterval(2)
        while !api.calls.contains(where: { $0.method == "chat.warm" }) && Date() < end { try? await Task.sleep(for: .milliseconds(5)) }
        XCTAssertEqual(api.calls.filter { $0.method == "chat.warm" }.count, 1)
        XCTAssertTrue(api.calls.first { $0.method == "chat.warm" }!.params.contains("\"voice\":true"))
    }

    @MainActor
    func testWarmCacheShrugsOffAFailingEngine() async {
        let api = FakeAPI()
        api.failing = ["chat.warm"]
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.warmCache()                                    // returns at once and does not throw or crash
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(v.presenceVoice, "idle")
    }

    @MainActor
    func testNoWarmInClassMode() async {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.classMode = true
        v.warmCache()
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertTrue(api.calls.filter { $0.method == "chat.warm" }.isEmpty)
    }
}
