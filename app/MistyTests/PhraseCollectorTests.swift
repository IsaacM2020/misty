import XCTest
@testable import MistyCore

/// F6: the voice code hands each heard final to the engine's local collector, and only under the rules. Fake API, fake frames.
@MainActor
final class PhraseCollectorTests: XCTestCase {
    func make(collecting: Bool) -> (VoiceVM, FakeAPI) {
        let api = FakeAPI()
        api.responses["phrases.state"] = collecting ? "{\"collecting\":true}" : "{\"collecting\":false}"
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.speakReplies = false
        v.testFront("com.apple.Safari")
        return (v, api)
    }

    func settle() async { try? await Task.sleep(for: .milliseconds(200)) }
    func adds(_ api: FakeAPI) -> [(method: String, params: String)] { api.calls.filter { $0.method == "phrases.add" } }

    func testOffByDefaultSendsNoWords() async {
        let (v, api) = make(collecting: false)
        v.testStartAmbient(.name)
        v.testHeard(.final("pass me the first aid kit"))
        await settle()
        XCTAssertEqual(adds(api).count, 0)                                         // the engine said off: the words never leave the app
        XCTAssertEqual(api.calls.filter { $0.method == "phrases.state" }.count, 1)
    }

    func testOnHandsOverADroppedPhraseWithItsFacts() async {
        let (v, api) = make(collecting: true)
        v.testStartAmbient(.name)
        v.testHeard(.final("pass me the first aid kit"))
        await settle()
        let a = adds(api)
        XCTAssertEqual(a.count, 1)
        XCTAssertTrue(a[0].params.contains("pass me the first aid kit"))
        XCTAssertTrue(a[0].params.contains("\"outcome\":\"dropped\""))
        XCTAssertTrue(a[0].params.contains("\"state\":\"listening\""))
        XCTAssertTrue(a[0].params.contains("\"named\":false"))
    }

    func testADeliveredPhraseIsMarkedDeliveredAndNamed() async {
        let (v, api) = make(collecting: true)
        v.testStartAmbient(.name)
        v.testHeard(.final("Edith, what time is it"))
        await settle()
        let a = adds(api)
        XCTAssertEqual(a.count, 1)
        XCTAssertTrue(a[0].params.contains("\"outcome\":\"delivered\""))
        XCTAssertTrue(a[0].params.contains("\"named\":true"))
    }

    func testTheJudgesAnswerBecomesTheOutcome() async {
        let (v, api) = make(collecting: true)
        api.responses["live.hear"] = "{\"to\":\"someone_else\",\"finished\":true}"
        v.testStartAmbient(.off)
        v.testHeard(.final("dinner is at eight tonight"))
        await settle()
        XCTAssertTrue(adds(api).first?.params.contains("\"outcome\":\"dropped\"") ?? false)
        let (v2, api2) = make(collecting: true)
        api2.responses["live.hear"] = "{\"to\":\"misty\",\"finished\":true}"
        v2.testStartAmbient(.off)
        v2.testHeard(.final("what is the weather like tomorrow"))
        await settle()
        XCTAssertTrue(adds(api2).first?.params.contains("\"outcome\":\"delivered\"") ?? false)
    }

    func testClassModeCollectsNothingAndEvenSkipsTheQuestion() async {
        let (v, api) = make(collecting: true)
        v.testStartAmbient(.off)
        v.classMode = true
        v.testHeard(.final("the mitochondria is the powerhouse of the cell"))
        await settle()
        XCTAssertEqual(adds(api).count, 0)
        XCTAssertEqual(api.calls.filter { $0.method == "phrases.state" }.count, 0)
    }

    func testAPrivateAppInFrontCollectsNothing() async {
        let (v, api) = make(collecting: true)
        let was = ContextFrame.privateBundles
        ContextFrame.privateBundles = ["com.1password.1password"]
        defer { ContextFrame.privateBundles = was }
        v.testFront("com.1password.1password")
        v.testStartAmbient(.name)
        v.testHeard(.final("my password is on the sticky note"))
        await settle()
        XCTAssertEqual(adds(api).count, 0)
        XCTAssertEqual(api.calls.filter { $0.method == "phrases.state" }.count, 0)
    }

    func testTheFollowUpWindowIsReported() async {
        let (v, api) = make(collecting: true)
        v.testStartAmbient(.name)
        v.testHeard(.final("Edith, hello"))
        await settle()
        XCTAssertTrue(adds(api).first?.params.contains("\"followup\":false") ?? false)
    }

    func testAskingTheEngineIsRareNotPerPhrase() async {
        let (v, api) = make(collecting: true)
        v.testStartAmbient(.name)
        for i in 0..<20 { v.testHeard(.final("room talk number \(i) here")) }
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "phrases.state" }.count, 1)
    }

    func testNoAudioApiExists() {
        // the only thing handed over is text: the note has no data or audio field
        let names = Mirror(reflecting: PhraseNote(text: "a", state: "listening", named: false, followup: false, sinceReply: nil, outcome: "dropped"))
            .children.compactMap { $0.label }
        XCTAssertEqual(Set(names), ["text", "state", "named", "followup", "sinceReply", "outcome"])
    }
}
