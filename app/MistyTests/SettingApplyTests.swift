import XCTest
@testable import MistyCore

@MainActor
final class FakeTarget: SettingTarget {
    var speakReplies = true
    var voices = ["Ember", "Juno"]
    var voiceName = "Ember"
    var stopped = 0
    func setVoice(_ name: String) async { voiceName = name }
    func stopSpeakingNow() { stopped += 1 }
    var ambientMode = "off"
    var refuseAmbient = false
    func setAmbient(_ mode: String) async -> Bool {
        if mode != "off" && refuseAmbient { return false }
        ambientMode = mode
        return true
    }
}

/// Card B6: the engine's `setting` tool changes the app's own mute and voice, and the answer carries what the app now holds.
@MainActor
final class SettingApplyTests: XCTestCase {
    func testMuteStopsHerAndUnmuteDoesNot() async {
        let t = FakeTarget()
        let r = await SettingApply.run(name: "speak_replies", value: .bool(false), on: t)
        XCTAssertTrue(r.ok); XCTAssertEqual(r.data["value"], .bool(false)); XCTAssertFalse(t.speakReplies); XCTAssertEqual(t.stopped, 1)
        let u = await SettingApply.run(name: "speak_replies", value: .bool(true), on: t)
        XCTAssertEqual(u.data["value"], .bool(true)); XCTAssertEqual(t.stopped, 1)
    }

    func testVoiceByNameIsCaseInsensitiveAndReportsTheNameItNowHolds() async {
        let t = FakeTarget()
        let r = await SettingApply.run(name: "voice", value: .string("juno"), on: t)
        XCTAssertTrue(r.ok); XCTAssertEqual(r.data["value"], .string("Juno")); XCTAssertEqual(t.voiceName, "Juno")
    }

    func testAnUnknownVoiceChangesNothingAndListsTheNames() async {
        let t = FakeTarget()
        let r = await SettingApply.run(name: "voice", value: .string("Nobody"), on: t)
        XCTAssertFalse(r.ok); XCTAssertEqual(t.voiceName, "Ember")
        XCTAssertEqual(r.data["voices"], .array([.string("Ember"), .string("Juno")]))
        let l = await SettingApply.run(name: "voice", value: .string("list"), on: t)
        XCTAssertTrue(l.ok); XCTAssertEqual(t.voiceName, "Ember")
    }

    func testAnythingElseIsRefused() async {
        let t = FakeTarget()
        for n in ["cap_daily_usd", "paused", ""] {
            let r = await SettingApply.run(name: n, value: .bool(true), on: t)
            XCTAssertFalse(r.ok); XCTAssertEqual(r.code, "NOT_ALLOWED")
        }
        let bad = await SettingApply.run(name: "speak_replies", value: .string("off"), on: t)
        XCTAssertEqual(bad.code, "BAD_VALUE"); XCTAssertTrue(t.speakReplies)
    }
}
