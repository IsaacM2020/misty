import XCTest
@testable import MistyCore

final class RadarTests: XCTestCase {
    @MainActor
    func testGroupsComeInTheOrderTheyAreShown() {
        XCTAssertEqual(RadarVM.groups.map(\.kind), ["goal", "interest", "person", "other"])
    }

    func testACareDecodesFromTheEnginesAnswer() throws {
        let json = #"{"id":"c_1","key":"cricket","kind":"interest","text":"loves cricket","weight":3,"level":"matters a lot","updated_at":1.0}"#
        let c = try Wire.decoder().decode(Care.self, from: Data(json.utf8))
        XCTAssertEqual(c.key, "cricket")
        XCTAssertEqual(c.weight, 3)
        XCTAssertEqual(c.level, "matters a lot")
    }
}

extension RadarTests {
    func testAWatchDecodesFromTheEnginesAnswer() throws {
        let json = #"{"id":"fl_1","text":"India vs NZ","source":"web","every_min":30,"until":"Sun 15 Nov 11:13","checks":2,"last_error":null,"last_checked":"Sun 08 Nov 11:40","latest":"Cricinfo reports rain.","latest_source":"cricinfo.com"}"#
        let w = try Wire.decoder().decode(Watch.self, from: Data(json.utf8))
        XCTAssertEqual(w.everyMin, 30)
        XCTAssertEqual(w.latestSource, "cricinfo.com")
        XCTAssertNil(w.lastError)
    }
}

extension RadarTests {
    @MainActor
    func testToldYouLoadsAndAThumbIsKept() async throws {
        let api = FakeAPI()
        api.responses["told.list"] = #"{"told":[{"id":"td_1","at":1.0,"topic_text":"the match","said":"Cricinfo reports rain.","source_name":"Cricinfo","how":"say","feedback":null}],"held":[{"id":"hd_1"}]}"#
        api.responses["told.rate"] = #"{"ok":true}"#
        let vm = RadarVM(api: api)
        await vm.load()
        XCTAssertEqual(vm.told.first?.said, "Cricinfo reports rain.")
        XCTAssertEqual(vm.held, 1)
        await vm.rate("td_1", "down")
        XCTAssertEqual(vm.told.first?.feedback, "down")
        XCTAssertTrue(api.calls.contains { $0.method == "told.rate" && $0.params.contains("\"value\":\"down\"") })
    }
}

extension RadarTests {
    @MainActor
    func testLessonsLoad() async {
        let api = FakeAPI()
        api.responses["lessons.list"] = #"{"lessons":[{"id":"ls_1","n":1,"text":"Don't tell him about rain delays.","source":"feedback","created_at":1.0}],"last_replay":null}"#
        let vm = RadarVM(api: api)
        await vm.load()
        XCTAssertEqual(vm.lessons.map(\.text), ["Don't tell him about rain delays."])
    }
}

extension RadarTests {
    @MainActor
    func testWaysLoadAndTheDefaultTagFollowsOrigin() async {
        let api = FakeAPI()
        api.responses["ways.list"] = #"{"ways":[{"id":"w_1","key":"length","kind":"habit","text":"Short and plain.","value":"","origin":"default","updated_at":1.0},{"id":"w_2","key":"address","kind":"address","text":"Call him boss.","value":"boss","origin":"chat","updated_at":2.0}]}"#
        let vm = RadarVM(api: api)
        await vm.load()
        XCTAssertEqual(vm.ways.map(\.text), ["Short and plain.", "Call him boss."])
        XCTAssertEqual(vm.ways.map(\.isDefault), [true, false])
    }

    @MainActor
    func testDidForYouAndProtocolsLoad() async {
        let api = FakeAPI()
        api.responses["moves.list"] = #"{"moves":[{"id":2,"at":2.0,"name":"timer_stop","what":"stopped 1 timer","risk":"safe","asked":false,"answer":"","verified":true,"undoable":false,"protocol":"","source":"him"},{"id":1,"at":1.0,"name":"timer_start","what":"started a 25 minute timer","risk":"safe","asked":false,"answer":"","verified":null,"undoable":true,"protocol":"","source":"him"}]}"#
        api.responses["protocols.list"] = #"{"protocols":[{"key":"math mode","triggers":["lets do math"],"steps":["open app (name Notes)","timer start (minutes 45)"],"end_steps":[],"runs":2}]}"#
        let vm = RadarVM(api: api)
        await vm.load()
        XCTAssertEqual(vm.did.map(\.title), ["timer stop", "timer start"])
        XCTAssertEqual(vm.did.map(\.isVerified), [true, false])
        XCTAssertTrue(vm.did[1].undoable)
        XCTAssertEqual(vm.protocols.first?.steps.count, 2)
        XCTAssertEqual(vm.protocols.first?.runs, 2)
    }

    @MainActor
    func testARadarWithNoWaysEndpointStillLoads() async {
        let vm = RadarVM(api: FakeAPI())
        await vm.load()
        XCTAssertTrue(vm.loaded); XCTAssertTrue(vm.ways.isEmpty)
    }
}

extension RadarTests {
    static let learningJSON = #"{"courses":[{"course":"co_1","subject":"Bread","goal":"bake a loaf","by":"2026-12-08","days_left":21,"status":"active","topics":4,"solid":1,"shaky":2,"due":1,"next":{"topic":"Feeding a starter","day":"2026-11-17","kind":"new"}}],"shaky":[{"topic":"Proofing","subject":"Bread","level":"shaky","why":"you got one wrong 1 day ago"}],"due":[{"topic":"Proofing","subject":"Bread","level":"shaky"}],"probes":[{"topic":"Feeding a starter","line":"1 of 3 before, 3 of 3 now."}],"drills":{"days":7,"count":3,"by_kind":{"drill":2,"review":1},"right":9,"wrong":2},"mistakes":{"total":5,"week":3,"retired":1,"flagged":0,"by_kind":{"slipped":5},"pattern":""}}"#

    func testTheLearningViewDecodesFromTheEnginesAnswer() throws {
        let l = try Wire.decoder().decode(Learning.self, from: Data(Self.learningJSON.utf8))
        XCTAssertEqual(l.courses.first?.daysLeft, 21)
        XCTAssertEqual(l.courses.first?.fraction, 0.25)
        XCTAssertEqual(l.shaky.first?.why, "you got one wrong 1 day ago")
        XCTAssertNil(l.due.first?.why)
        XCTAssertEqual(l.probes.first?.line, "1 of 3 before, 3 of 3 now.")
        XCTAssertEqual(l.mistakes.retired, 1)
        XCTAssertFalse(l.isEmpty)
    }

    @MainActor
    func testLearningLoadsIntoTheRadar() async {
        let api = FakeAPI()
        api.responses["teach.overview"] = Self.learningJSON
        let vm = RadarVM(api: api)
        await vm.load()
        XCTAssertEqual(vm.learning?.courses.map(\.subject), ["Bread"])
        XCTAssertTrue(api.calls.contains { $0.method == "teach.overview" })
    }

    @MainActor
    func testAPersonWhoHasLearnedNothingGetsNoLearningSection() async {
        let api = FakeAPI()
        api.responses["teach.overview"] = #"{"courses":[],"shaky":[],"due":[],"probes":[],"drills":{"days":7,"count":0,"by_kind":{},"right":0,"wrong":0},"mistakes":{"total":0,"week":0,"retired":0,"flagged":0,"by_kind":{},"pattern":""}}"#
        let vm = RadarVM(api: api)
        await vm.load()
        XCTAssertNil(vm.learning)
        XCTAssertTrue(vm.loaded)
    }

    @MainActor
    func testAnOlderEngineWithNoLearningMethodStillLoads() async {
        let vm = RadarVM(api: FakeAPI())
        await vm.load()
        XCTAssertNil(vm.learning)
    }

    func testTheLinesAreInPlainWords() {
        let c = LearningCourse(course: "c", subject: "Bread", goal: "", by: "2026-12-08", daysLeft: 1, status: "active", topics: 4, solid: 0, shaky: 0, due: 0, next: nil)
        XCTAssertEqual(LearningWords.when(c), "2026-12-08, 1 day left")
        var late = c; late.daysLeft = -3
        XCTAssertEqual(LearningWords.when(late), "2026-12-08, 3 days over")
        var nodate = c; nodate.daysLeft = nil
        XCTAssertEqual(LearningWords.when(nodate), "no date")
        XCTAssertEqual(LearningWords.next(LearningNext(topic: "Proofing", day: "2026-11-18", kind: "review")), "Next: a quick drill on Proofing, 2026-11-18")
        XCTAssertEqual(LearningWords.drills(LearningDrills(days: 7, count: 1, right: 2, wrong: 0)), "1 drill in 7 days: 2 right, 0 wrong.")
        XCTAssertEqual(LearningWords.mistakes(LearningMistakes(total: 1, week: 1, retired: 0, flagged: 0, pattern: "")), "1 mistake saved, 1 this week.")
        XCTAssertEqual(c.fraction, 0)
    }
}
