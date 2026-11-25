import XCTest
@testable import MistyCore

/// Chunked reading: while he talks, what he has said is read at each short pause, so the end of the phrase only reads the tail.
/// A fake reader stands in for Fluid Speech Mini. Nothing here is Isaac's real speech.
final class ChunkedReadTests: XCTestCase {
    func tone(_ s: Double) -> [Float] { (0..<Int(s * 16000)).map { 0.1 * sin(Float($0) * 0.12) } }
    func zeros(_ s: Double) -> [Float] { [Float](repeating: 0, count: Int(s * 16000)) }

    final class Rig: @unchecked Sendable {
        let ears = Ears()
        private let lock = NSLock()
        private var _finals: [String] = [], _chunks: [Int] = [], _tails: [Int] = []
        var chunkResult: String? = "first part"
        var tailResult = "the rest"
        var finals: [String] { lock.lock(); defer { lock.unlock() }; return _finals }
        var chunkLengths: [Int] { lock.lock(); defer { lock.unlock() }; return _chunks }
        var tailLengths: [Int] { lock.lock(); defer { lock.unlock() }; return _tails }
        init() {
            ears.chunkReader = { [unowned self] w in self.lock.lock(); self._chunks.append(w.count); let r = self.chunkResult; self.lock.unlock(); return r }
            ears.testReader = { [unowned self] w in self.lock.lock(); self._tails.append(w.count); let r = self.tailResult; self.lock.unlock(); return r }
            ears.onFrame = { [unowned self] f in
                if case .final(let t) = f { self.lock.lock(); self._finals.append(t); self.lock.unlock() }
            }
            ears.feed([Float](repeating: 0, count: 16000))
        }
        func drain() { ears._drainForTests() }
        /// Quiet arrives in small buffers, like the real mic, so the ears see each stretch of the pause.
        func quiet(_ seconds: Double) { for _ in 0..<Int((seconds / 0.05).rounded()) { ears.feed([Float](repeating: 0, count: 800)) } }
    }

    func testAPauseInsideAPhraseCutsAChunkAndTheEndOnlyReadsTheTail() {
        let r = Rig()
        r.ears.feed(tone(2.0))                 // two seconds of talking
        r.quiet(0.30)                          // a 300 ms pause: a chunk is cut here
        r.ears.feed(tone(1.0))                 // he carries on
        r.quiet(1.0)                           // the phrase ends
        r.drain(); Thread.sleep(forTimeInterval: 0.3); r.drain()
        XCTAssertEqual(r.chunkLengths.count, 1)
        XCTAssertGreaterThanOrEqual(r.chunkLengths[0], 32000)            // the first part, at least 2 s
        XCTAssertEqual(r.finals, ["first part the rest"])
        XCTAssertLessThan(r.tailLengths.last ?? .max, 16000 * 2)          // the final read was the tail, not the whole phrase
    }

    func testAShortPhraseIsNotChunked() {
        let r = Rig()
        r.ears.feed(tone(0.3)); r.quiet(0.30); r.ears.feed(tone(0.1)); r.quiet(1.0)
        r.drain(); Thread.sleep(forTimeInterval: 0.3); r.drain()
        XCTAssertEqual(r.chunkLengths.count, 0)                          // never 1.5 s of audio at a pause: no chunk
        XCTAssertEqual(r.finals.count, 1)
    }

    func testTheQuietAtTheEndIsNotReadAsATail() {
        let r = Rig()
        r.ears.feed(tone(2.0)); r.quiet(1.0)                                // chunk at 250 ms, then only silence to the end
        r.drain(); Thread.sleep(forTimeInterval: 0.3); r.drain()
        XCTAssertEqual(r.chunkLengths.count, 1)
        XCTAssertTrue(r.tailLengths.allSatisfy { $0 >= 32000 })           // only the early-end decision read (whole phrase); no tail read of the quiet
        XCTAssertEqual(r.finals, ["first part"])
    }

    func testAFailedChunkMeansTheWholePhraseIsReadAgain() {
        let r = Rig()
        r.chunkResult = nil
        r.tailResult = "whole phrase"
        r.ears.feed(tone(2.0)); r.quiet(0.30); r.ears.feed(tone(1.0)); r.quiet(1.0)
        r.drain(); Thread.sleep(forTimeInterval: 0.3); r.drain()
        XCTAssertEqual(r.finals, ["whole phrase"])
        XCTAssertGreaterThanOrEqual(r.tailLengths.last ?? 0, 16000 * 3)   // the full phrase went through the reader
    }
}
