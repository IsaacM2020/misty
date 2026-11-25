import AVFoundation
import XCTest
@testable import MistyCore

/// Live transcript v2 in the app: Phonon-2 stays the instant preview, and every so often the engine hands back lines
/// Phonon-2.5 read again, which replace the preview lines on the same timeline. One round at a time.
final class ClassLinesTests: XCTestCase {
    private func seg(_ id: Int, _ a: Double, _ b: Double, _ src: String, _ text: String) -> ClassSegment {
        ClassSegment(id: id, tStart: a, tEnd: b, source: src, text: text)
    }

    func testCorrectedLinesReplaceThePreviewAndKeepTheOrderAndLabels() {
        let before = [seg(1, 0, 4, "mac", "aboga dros"), seg(2, 6, 8, "room", "wat is that"), seg(3, 9, 12, "mac", "of particles")]
        let change = ClassChange(removed: [1, 3], added: [seg(10, 0.2, 4.5, "mac", "Avogadro's number"),
                                                          seg(11, 9, 12, "mac", "of particles.")])
        let after = ClassLines.apply(before, [change])
        XCTAssertEqual(after.map(\.text), ["Avogadro's number", "wat is that", "of particles."])
        XCTAssertEqual(after.map(\.source), ["mac", "room", "mac"])              // Teacher / Me labels come from the source
    }

    func testNothingChangesWhenTheEngineSendsNoChanges() {
        let before = [seg(1, 0, 4, "mac", "keep me")]
        XCTAssertEqual(ClassLines.apply(before, []), before)
    }

    func testALineTheEngineNoLongerHasIsNotLeftBehind() {
        let after = ClassLines.apply([seg(1, 0, 4, "mac", "old")], [ClassChange(removed: [1, 99], added: [])])
        XCTAssertTrue(after.isEmpty)                                            // an unknown id is just ignored
    }

    /// A preview line that arrives late for a stretch the model already corrected must not be added twice.
    func testLatePreviewInsideTheCorrectedStretchIsSkipped() {
        XCTAssertTrue(ClassLines.isCovered(tStart: 40, tEnd: 44, upTo: 60))
        XCTAssertFalse(ClassLines.isCovered(tStart: 58, tEnd: 66, upTo: 60))    // its middle (62) is after the cut
        XCTAssertFalse(ClassLines.isCovered(tStart: 10, tEnd: 12, upTo: 0))     // nothing corrected yet
    }

    func testTheEnginesAnswerDecodes() throws {
        let json = #"{"changes":[{"removed":[1,2],"added":[{"id":7,"t_start":0.5,"t_end":3.0,"source":"mac","text":"Hello"}]}],"up_to":46.0}"#
        let r = try Wire.decoder().decode(ClassCorrection.self, from: Data(json.utf8))
        XCTAssertEqual(r.upTo, 46)
        XCTAssertEqual(r.changes.first?.added.first?.tStart, 0.5)
        XCTAssertNil(r.busy)
    }
}

/// An engine that holds `class.correct` until told to answer, to prove a second round is skipped while one runs.
private final class GatedAPI: EngineAPI, @unchecked Sendable {
    private let lock = NSLock()
    private var cont: CheckedContinuation<Void, Never>?
    private(set) var correctCalls = 0
    var hold = false
    var answer = #"{"changes":[{"removed":[],"added":[{"id":50,"t_start":0,"t_end":2,"source":"mac","text":"Corrected."}]}],"up_to":46}"#

    func call<P: Encodable & Sendable, R: Decodable & Sendable>(_ method: String, _ params: P) async throws -> R {
        var json = "{}"
        switch method {
        case "class.start":
            json = #"{"id":"cs_1","subject":"Chem","title":"Chem, 16 Nov","started_at":1794800000,"status":"live","his_notes":"","notes":null,"gaps":[],"pruned":0,"mode":"online"}"#
        case "class.segment": json = #"{"id":1}"#
        case "class.correct":
            let wait: Bool = lock.withLock { correctCalls += 1; return hold }
            if wait { await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in lock.withLock { cont = c } } }
            json = answer
        default: break
        }
        return try Wire.decoder().decode(R.self, from: Data(json.utf8))
    }

    func release() { let c: CheckedContinuation<Void, Never>? = lock.withLock { let c = cont; cont = nil; return c }; c?.resume() }
}

@MainActor
final class ClassLiveVMTests: XCTestCase {
    private func online() async -> (ClassVM, GatedAPI) {
        let api = GatedAPI()
        let vm = ClassVM(api: api, now: { Date(timeIntervalSince1970: 1_794_800_000) })
        vm.listen = { _, _ in true }
        vm.mode = .online
        await vm.start(subject: "Chem")
        return (vm, api)
    }

    func testCorrectedLinesAppearInTheTranscript() async {
        let (vm, _) = await online()
        await vm.heard("aboga dros", source: "mac")
        await vm.correctNow()
        XCTAssertTrue(vm.segments.contains { $0.text == "Corrected." && $0.source == "mac" })
        XCTAssertEqual(vm.correctedUpTo, 46)
    }

    func testAPreviewLineForAnAlreadyCorrectedStretchIsNotSent() async {
        let (vm, _) = await online()
        await vm.correctNow()                                                   // corrected up to 46 s
        let before = vm.segments.count
        await vm.heard("late preview", source: "mac")                           // heard "now" = class time 0, inside 0..46
        XCTAssertEqual(vm.segments.count, before)
    }

    func testASecondRoundIsSkippedWhileOneIsRunning() async {
        let (vm, api) = await online()
        api.hold = true
        let first = Task { await vm.correctNow() }
        try? await Task.sleep(for: .milliseconds(50))
        await vm.correctNow()                                                   // the first is still running
        XCTAssertEqual(api.correctCalls, 1)
        api.release()
        await first.value
        api.hold = false
        await vm.correctNow()
        XCTAssertEqual(api.correctCalls, 2)                                     // free again, so the next round runs
    }

    func testTheTimerAsksOnlyInAnOnlineClassAndOnlyEveryNSeconds() async {
        let (vm, api) = await online()
        vm.correctEvery = 3
        await vm.tick(); await vm.tick()
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(api.correctCalls, 0)
        await vm.tick()                                                         // the third second
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(api.correctCalls, 1)
    }

    func testARoomClassNeverAsks() async {
        let api = GatedAPI()
        let vm = ClassVM(api: api, now: { Date(timeIntervalSince1970: 1_794_800_000) })
        vm.listen = { _, _ in true }
        vm.mode = .room
        await vm.start(subject: "Chem")
        vm.correctEvery = 1
        await vm.tick(); await vm.tick()
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(api.correctCalls, 0)
    }
}

/// The engine can only read a live track as plain samples (an m4a opens only once it is closed), so each track is also
/// written as 16 kHz 16-bit mono `.pcm` with a `.start` file, and the scratch goes once the m4a has been checked.
final class ClassPCMTests: XCTestCase {
    private func dir() -> URL {
        let d = FileManager.default.temporaryDirectory.appendingPathComponent("pcm-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    func testTheTrackIsAlsoWrittenAsPlainSamplesWhileItRuns() {
        let d = dir()
        let track = ClassTrack(url: d.appendingPathComponent("class.m4a"), now: { Date(timeIntervalSince1970: 1_800_000_000) })
        track.append([Float](repeating: 0.5, count: 16000))
        let deadline = Date().addingTimeInterval(3)
        var size = 0
        while Date() < deadline {
            size = (try? FileManager.default.attributesOfItem(atPath: track.pcmURL.path)[.size] as? Int) ?? 0
            if size >= 32000 { break }
            Thread.sleep(forTimeInterval: 0.02)
        }
        XCTAssertEqual(size, 32000)                                             // one second, 2 bytes a sample
        let data = try! Data(contentsOf: track.pcmURL)
        let first = data.withUnsafeBytes { $0.load(as: Int16.self) }
        XCTAssertEqual(Int(first), 16383, accuracy: 2)                          // 0.5 of full scale, little endian
        let start = try! String(contentsOf: track.startURL, encoding: .utf8)
        XCTAssertEqual(Double(start)!, 1_800_000_000, accuracy: 0.001)
        _ = track.close()
    }

    func testScratchGoesOnlyAfterTheM4AOpens() {
        let d = dir()
        let track = ClassTrack(url: d.appendingPathComponent("class.m4a"))
        track.append([Float](repeating: 0.2, count: 32000))
        _ = track.close()
        XCTAssertNotNil(try? AVAudioFile(forReading: track.url), "the m4a is finished and opens")
        XCTAssertFalse(FileManager.default.fileExists(atPath: track.pcmURL.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: track.startURL.path))
    }

    func testAnM4AThatDoesNotOpenKeepsItsScratch() {
        let d = dir()
        let junk = d.appendingPathComponent("class.m4a")
        try? Data("junk".utf8).write(to: junk)
        XCTAssertFalse(ClassTrack.m4aOpens(junk))                               // so close() would leave the .pcm alone
        XCTAssertFalse(ClassTrack.m4aOpens(d.appendingPathComponent("missing.m4a")))
        let ok = ClassTrack(url: d.appendingPathComponent("ok.m4a"))
        ok.append([Float](repeating: 0.2, count: 32000))
        _ = ok.close()
        XCTAssertTrue(ClassTrack.m4aOpens(ok.url))
    }
}
