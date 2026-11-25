import AVFoundation
import XCTest
@testable import MistyCore

/// 16 Nov 2026, class cs_4dbbaeed: ten seconds in, the Mac's audio route changed (the mic device went from 1 to 3
/// input channels). AVAudioEngine stops itself when that happens and says so with a notification; `AudioIO` never
/// listened, so Isaac's mic track froze at 64 KB while the teacher track carried on. The restart rule lives in
/// `RouteRestart` so it can be tested without a real microphone.
final class AudioRouteRestartTests: XCTestCase {
    private struct Boom: Error {}

    /// A route change stops the dead engine once and opens the mic again.
    func testRouteChangeStopsThenReopensTheMic() {
        var events: [String] = []
        RouteRestart.run(stop: { events.append("stop") },
                         start: { events.append("start") },
                         retry: { _, again in again() },
                         log: { _ in })
        XCTAssertEqual(events, ["stop", "start"])
    }

    /// The device can still be settling (AirPods mid-switch): a failed reopen is tried again, not given up on.
    func testFailedReopenIsRetriedUntilItWorks() {
        var starts = 0
        var waits: [TimeInterval] = []
        RouteRestart.run(stop: {},
                         start: { starts += 1; if starts < 3 { throw Boom() } },
                         retry: { after, again in waits.append(after); again() },
                         log: { _ in })
        XCTAssertEqual(starts, 3)
        XCTAssertEqual(waits.count, 2)
        XCTAssertEqual(waits, waits.sorted(), "waits grow, never shrink")
    }

    /// It does not retry forever: after the last wait it gives up and says so in the log.
    func testGivesUpAfterTheLastAttemptAndLogsIt() {
        var starts = 0
        var lines: [String] = []
        RouteRestart.run(stop: {}, start: { starts += 1; throw Boom() }, retry: { _, again in again() }, log: { lines.append($0) })
        XCTAssertEqual(starts, RouteRestart.delays.count + 1)
        XCTAssertTrue(lines.last?.contains("gave up") == true, "the voice log must say the mic is gone: \(lines)")
    }

    /// A success is logged too, so the next time the log shows the mic came back (and after how many tries).
    func testSuccessIsLogged() {
        var lines: [String] = []
        RouteRestart.run(stop: {}, start: {}, retry: { _, again in again() }, log: { lines.append($0) })
        XCTAssertEqual(lines.count, 1)
        XCTAssertTrue(lines[0].contains("restarted"), lines[0])
    }

    /// Only the audio engine's own change counts, and only while it is meant to be running.
    func testOnlyRunningEngineOnItsOwnNotificationRestarts() {
        let mine = AVAudioEngine(), other = AVAudioEngine()
        XCTAssertTrue(RouteRestart.applies(running: true, optedIn: true, from: mine, mine: mine))
        XCTAssertFalse(RouteRestart.applies(running: true, optedIn: true, from: other, mine: mine), "another engine's change")
        XCTAssertFalse(RouteRestart.applies(running: false, optedIn: true, from: mine, mine: mine), "mic already stopped on purpose")
        XCTAssertFalse(RouteRestart.applies(running: true, optedIn: false, from: mine, mine: mine), "dictation restarts itself")
    }
}
