import XCTest
@testable import MistyCore

/// Granola-style: Misty notices a call by who is using the mic, and asks once per call.
final class MeetingDetectorTests: XCTestCase {
    func testNamesTheCallAppsAndIgnoresTheRest() {
        XCTAssertEqual(MeetingApps.name(forBundle: "us.zoom.xos"), "Zoom")
        XCTAssertEqual(MeetingApps.name(forBundle: "com.microsoft.teams2"), "Teams")
        XCTAssertEqual(MeetingApps.name(forBundle: "net.whatsapp.WhatsApp"), "WhatsApp")
        XCTAssertEqual(MeetingApps.name(forBundle: "com.apple.FaceTime"), "FaceTime")
        XCTAssertEqual(MeetingApps.name(forBundle: "com.google.Chrome.helper.Renderer"), "Chrome")   // Meet runs in a browser
        XCTAssertNil(MeetingApps.name(forBundle: "com.isaacmiranda.Misty"))                          // her own mic
        XCTAssertNil(MeetingApps.name(forBundle: "com.apple.voicememos"))
        XCTAssertNil(MeetingApps.name(forBundle: "org.hammerspoon.Hammerspoon"))                     // push-to-talk dictation
    }

    func testPicksTheFirstCallAppUsingTheMic() {
        let users = [MicUser(bundleID: "com.apple.voicememos"), MicUser(bundleID: "us.zoom.xos")]
        XCTAssertEqual(MeetingApps.inCall(users), "Zoom")
        XCTAssertNil(MeetingApps.inCall([MicUser(bundleID: "com.isaacmiranda.Misty")]))
    }

    func testAsksOnlyAfterTheMicHasBeenInUseForAWhile() {
        var w = MeetingWatch()
        let t0 = Date(timeIntervalSince1970: 1_800_000_000)
        XCTAssertNil(w.step(app: "Zoom", now: t0))
        XCTAssertNil(w.step(app: "Zoom", now: t0 + 5))                 // a beep, not a call, so far
        XCTAssertEqual(w.step(app: "Zoom", now: t0 + 10), "Zoom")
    }

    func testAsksOncePerCall() {
        var w = MeetingWatch()
        let t0 = Date(timeIntervalSince1970: 1_800_000_000)
        _ = w.step(app: "Zoom", now: t0)
        XCTAssertEqual(w.step(app: "Zoom", now: t0 + 12), "Zoom")
        XCTAssertNil(w.step(app: "Zoom", now: t0 + 30))
        XCTAssertNil(w.step(app: nil, now: t0 + 40))                   // muted for a moment: still the same call
        XCTAssertNil(w.step(app: "Zoom", now: t0 + 50))
    }

    func testAsksAgainForTheNextCall() {
        var w = MeetingWatch()
        let t0 = Date(timeIntervalSince1970: 1_800_000_000)
        _ = w.step(app: "Zoom", now: t0)
        XCTAssertEqual(w.step(app: "Zoom", now: t0 + 12), "Zoom")
        XCTAssertNil(w.step(app: nil, now: t0 + 20))
        XCTAssertNil(w.step(app: nil, now: t0 + 90))                   // a minute of quiet: the call is over
        XCTAssertNil(w.step(app: "Teams", now: t0 + 100))
        XCTAssertEqual(w.step(app: "Teams", now: t0 + 111), "Teams")
    }

    func testAShortBlipThatStopsNeverAsks() {
        var w = MeetingWatch()
        let t0 = Date(timeIntervalSince1970: 1_800_000_000)
        _ = w.step(app: "Chrome", now: t0)
        XCTAssertNil(w.step(app: nil, now: t0 + 4))
        XCTAssertNil(w.step(app: nil, now: t0 + 70))
        XCTAssertNil(w.step(app: "Chrome", now: t0 + 75))              // the clock restarts, 10 s have not passed
    }

    @MainActor func testMeetingPromptShowsTheAskCardOnlyWhenNoClassIsRunning() async {
        let api = FakeAPI()
        api.responses["class.now"] = #"{"found":false}"#
        let vm = ClassVM(api: api)
        vm.listen = { _, _ in true }
        var shown = 0
        vm.onAsk = { shown += 1 }
        await vm.meetingDetected("Zoom")
        XCTAssertEqual(vm.ask?.title, "Zoom call")
        XCTAssertEqual(shown, 1)
        await vm.meetingDetected("Zoom")                               // the card is already up
        XCTAssertEqual(shown, 1)
    }

    @MainActor func testTheCalendarGuessNeverFlipsASubjectHePicked() async {
        let api = FakeAPI()
        api.responses["class.now"] = #"{"found":true,"title":"IB Chemistry HL","online":true,"subject":"Chem"}"#
        let vm = ClassVM(api: api)
        vm.pick("Physics")
        await vm.suggest()
        XCTAssertEqual(vm.subject, "Physics")
        XCTAssertEqual(vm.calTitle, "IB Chemistry HL")
    }

    @MainActor func testTheCalendarStillPicksTheSubjectWhenHeHasNot() async {
        let api = FakeAPI()
        api.responses["class.now"] = #"{"found":true,"title":"IB Chemistry HL","online":true,"subject":"Chem"}"#
        let vm = ClassVM(api: api)
        vm.subject = "Math"
        await vm.suggest()
        XCTAssertEqual(vm.subject, "Chem")
    }

    @MainActor func testRenameSendsOnlyWhatChanged() async {
        let api = FakeAPI()
        let vm = ClassVM(api: api)
        await vm.rename("cs_1", title: "Projectile motion", subject: nil)
        let call = api.calls.first { $0.method == "class.rename" }
        XCTAssertEqual(call?.params.contains("Projectile motion"), true)
        XCTAssertEqual(call?.params.contains("subject"), false)
    }
}
