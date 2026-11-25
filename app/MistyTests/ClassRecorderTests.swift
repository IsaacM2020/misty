import XCTest
@testable import MistyCore

final class TestClock: @unchecked Sendable {
    var t = Date(timeIntervalSince1970: 1_800_000_000)
    func advance(_ s: TimeInterval) { t += s }
}

final class ClassRecorderTests: XCTestCase {
    private func url() -> URL { FileManager.default.temporaryDirectory.appendingPathComponent("track-\(UUID().uuidString).m4a") }

    /// A lid-close pause keeps the same files open. Nothing arrives while the Mac sleeps, so the gap is filled with
    /// silence and what comes after still sits at its true time on the class clock (the after-class pass shifts by it).
    func testGapBetweenChunksBecomesSilenceSoTheTrackStaysOnTheClock() {
        let clock = TestClock()
        let track = ClassTrack(url: url(), now: { clock.t })
        track.append([Float](repeating: 0.1, count: 8000))             // half a second, then the lid closes
        clock.advance(20)                                                   // 20 s later the Mac wakes
        track.append([Float](repeating: 0.1, count: 8000))
        let (started, seconds) = track.close()!
        XCTAssertEqual(started.timeIntervalSince1970, 1_800_000_000, accuracy: 0.001)
        XCTAssertEqual(seconds, 20.5, accuracy: 0.05)                  // 20 s to the second chunk + its own 0.5 s
    }

    func testSteadyChunksAreWrittenAsTheyComeWithNoPadding() {
        let clock = TestClock()
        let track = ClassTrack(url: url(), now: { clock.t })
        for _ in 0..<50 {                                              // 50 x 100 ms
            track.append([Float](repeating: 0.1, count: 1600))
            clock.advance(0.1)
        }
        let (_, seconds) = track.close()!
        XCTAssertEqual(seconds, 5.0, accuracy: 0.01)
    }

    func testSmallJitterIsNotPadded() {
        let clock = TestClock()
        let track = ClassTrack(url: url(), now: { clock.t })
        track.append([Float](repeating: 0.1, count: 1600))
        clock.advance(0.4)                                                  // a slow callback, well under a second
        track.append([Float](repeating: 0.1, count: 1600))
        let (_, seconds) = track.close()!
        XCTAssertEqual(seconds, 0.2, accuracy: 0.01)
    }
}
