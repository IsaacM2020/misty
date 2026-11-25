import XCTest
@testable import MistyCore

/// Class sessions in the app: phrases go to the engine with times from the class start, the lid closing pauses,
/// 15 quiet minutes end the class, typing is saved once it settles, and class mode never reaches the chat.
@MainActor
final class ClassVMTests: XCTestCase {
    final class Clock: @unchecked Sendable { var t = Date(timeIntervalSince1970: 1_792_720_800) }

    func make() -> (ClassVM, FakeAPI, Clock) {
        let api = FakeAPI()
        api.responses["class.start"] = #"{"id":"cs_1","subject":"Chem","title":"Chem, 23 Oct","started_at":1792720800,"status":"live","his_notes":"","notes":null,"gaps":[],"pruned":0}"#
        api.responses["class.end"] = #"{"id":"cs_1","subject":"Chem","title":"Chem, 23 Oct","started_at":1792720800,"status":"ended","his_notes":"","notes":null,"gaps":[],"pruned":0}"#
        api.responses["class.segment"] = #"{"id":1}"#
        let clock = Clock()
        let vm = ClassVM(api: api, now: { clock.t })
        vm.listen = { _, _ in true }
        vm.saveDelay = .milliseconds(50)
        return (vm, api, clock)
    }

    func params(_ api: FakeAPI, _ m: String) -> [String] { api.calls.filter { $0.method == m }.map(\.params) }

    func testHeardSendsRelativeTimes() async {
        let (vm, api, clock) = make()
        await vm.start(subject: "Chem")
        XCTAssertEqual(vm.state, .live)
        clock.t += 62
        vm.partial("Le Chat")
        clock.t += 3
        await vm.heard("Le Chatelier shifts it")
        let p = params(api, "class.segment")
        XCTAssertEqual(p.count, 1)
        XCTAssertTrue(p[0].contains(#""t_start":62"#) && p[0].contains(#""t_end":65"#), p[0])
        XCTAssertTrue(p[0].contains(#""source":"room""#))
        XCTAssertEqual(vm.segments.last?.text, "Le Chatelier shifts it")
        XCTAssertEqual(vm.partialLine, "")
    }

    func testSleepPausesAndWakeResumes() async {
        let (vm, api, _) = make()
        await vm.start(subject: "Chem")
        await vm.systemWillSleep()
        XCTAssertEqual(vm.state, .paused("lid"))
        XCTAssertTrue(params(api, "class.pause").first?.contains(#""reason":"lid""#) ?? false)
        await vm.systemDidWake()
        XCTAssertEqual(vm.state, .live)
        XCTAssertEqual(params(api, "class.resume").count, 1)
    }

    func testFifteenMinutesQuietEnds() async {
        let (vm, api, clock) = make()
        await vm.start(subject: "Chem")
        clock.t += 14 * 60
        await vm.tick()
        XCTAssertEqual(vm.state, .live)
        clock.t += 61
        await vm.tick()
        XCTAssertEqual(vm.state, .idle)
        XCTAssertEqual(params(api, "class.end").count, 1)
        XCTAssertEqual(vm.endedNote, "Class ended (nothing heard for 15 min)")
    }

    func testNotesSaveDebounced() async throws {
        let (vm, api, _) = make()
        await vm.start(subject: "Chem")
        vm.notesChanged("a")
        vm.notesChanged("ab")
        vm.notesChanged("abc")
        try await Task.sleep(for: .milliseconds(200))
        let p = params(api, "class.notes.save")
        XCTAssertEqual(p.count, 1)
        XCTAssertTrue(p[0].contains(#""text":"abc""#))
    }

    func testLineTimerAddedWhenALineStarts() async {
        let (vm, _, clock) = make()
        await vm.start(subject: "Chem")
        clock.t += 75
        XCTAssertEqual(vm.stamped("le chat"), "[01:15] le chat")
        XCTAssertEqual(vm.stamped("[01:15] le chat\nnext"), "[01:15] le chat\n[01:15] next")
        XCTAssertEqual(vm.stamped("[01:15] le chat\n"), "[01:15] le chat\n")
    }

    func testLastPhraseLandsWhenTheClassEnds() async {
        let (vm, api, _) = make()
        await vm.start(subject: "Chem")
        vm.listen = { [weak vm] on, _ in
            if !on { await vm?.heard("bring calculators on Monday") }    // the ears hand over the phrase in flight
            return true
        }
        await vm.end()
        let segs = params(api, "class.segment")
        XCTAssertEqual(segs.count, 1)
        let order = api.calls.map(\.method).filter { $0 == "class.segment" || $0 == "class.end" }
        XCTAssertEqual(order, ["class.segment", "class.end"])
    }

    func testNotesEventDecodes() throws {
        let e = try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"class.notes","params":{"session":"cs_1","status":"noted"}}"#.utf8))
        XCTAssertEqual(e, .classNotes(session: "cs_1", status: "noted"))
    }

    func testClassModeFinalsGoToClassNotChat() async {
        let api = FakeAPI()
        api.responses["class.start"] = #"{"id":"cs_1","subject":"Chem","title":"Chem","started_at":1792720800,"status":"live","his_notes":"","notes":null,"gaps":[],"pruned":0}"#
        let chat = ChatVM(api: api)
        let voice = VoiceVM(api: api, chat: chat)
        let vm = ClassVM(api: api)
        vm.listen = { _, _ in true }
        voice.classSink = vm
        await vm.start(subject: "Chem")
        voice.classMode = true
        voice.testHeard(.final("Le Chatelier shifts equilibrium"))
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(api.calls.filter { $0.method == "chat.send" || $0.method == "live.hear" }.count, 0)
        XCTAssertEqual(api.calls.filter { $0.method == "class.segment" }.count, 1)
    }

    // MARK: online classes (the Mac's sound is the teacher, the mic is Isaac)

    func testOnlineClassStartsWithModeAndLabelsBothSides() async {
        let (vm, api, clock) = make()
        var modes: [ClassVM.Mode] = []
        vm.listen = { _, m in modes.append(m); return true }
        vm.mode = .online
        await vm.start(subject: "Chem")
        XCTAssertTrue(params(api, "class.start").first?.contains(#""mode":"online""#) ?? false)
        XCTAssertEqual(modes, [.online])
        clock.t += 10
        vm.partial("today we", source: "mac")
        XCTAssertEqual(vm.partialSource, "mac")
        await vm.heard("today we look at moles", source: "mac")
        clock.t += 4
        await vm.heard("is that per litre", source: "room")
        let p = params(api, "class.segment")
        XCTAssertTrue(p[0].contains(#""source":"mac""#) && p[1].contains(#""source":"room""#))
        XCTAssertEqual(vm.segments.map(\.source), ["mac", "room"])
        XCTAssertEqual(ClassWho.label(source: "mac", online: true), "Teacher")
        XCTAssertEqual(ClassWho.label(source: "room", online: true), "Me")
        XCTAssertNil(ClassWho.label(source: "room", online: false))
    }

    func testTeacherAndMicPhrasesKeepTheirOwnStartTimes() async {
        let (vm, api, clock) = make()
        vm.mode = .online
        await vm.start(subject: "Chem")
        clock.t += 20
        vm.partial("so the mole", source: "mac")
        clock.t += 5
        vm.partial("wait", source: "room")                 // Isaac cuts in while the teacher's phrase is open
        clock.t += 2
        await vm.heard("so the mole is an amount", source: "mac")
        let p = params(api, "class.segment")
        XCTAssertTrue(p[0].contains(#""t_start":20"#), p[0])
    }

    func testMacProblemShowsOnlyInOnlineClass() async {
        let (vm, _, _) = make()
        vm.macCheck = { "Not hearing the Mac's sound yet." }
        vm.mode = .online
        await vm.start(subject: "Chem")
        await vm.tick()
        XCTAssertEqual(vm.macProblem, "Not hearing the Mac's sound yet.")
        await vm.end()
        XCTAssertNil(vm.macProblem)
        vm.mode = .room
        await vm.start(subject: "Chem")
        await vm.tick()
        XCTAssertNil(vm.macProblem)
    }

    func testSuggestUsesCalendarVideoLink() async {
        let (vm, api, _) = make()
        api.responses["class.now"] = #"{"found":true,"title":"Chem tutor","online":true,"link":"https://meet.google.com/x","subject":"Chem","cal_event_id":null}"#
        await vm.suggest()
        XCTAssertEqual(vm.mode, .online)
        XCTAssertEqual(vm.subject, "Chem")
        XCTAssertEqual(vm.hint, "Chem tutor is on your calendar with a video link")
        api.responses["class.now"] = #"{"found":false}"#
        await vm.suggest()
        XCTAssertNil(vm.hint)
    }

    // MARK: reaching out (the engine's `suggest`)

    private func nowJSON(_ suggest: String, key: String, online: Bool = true, subject: String? = "Physics") -> String {
        let subj = subject.map { "\"\($0)\"" } ?? "null"
        return #"{"found":true,"title":"Prashant Class","online":\#(online),"subject":\#(subj),"starts_in":60,"key":"\#(key)","is_class":true,"suggest":"\#(suggest)"}"#
    }

    func testAutoSuggestStartsTheClassAndTellsTheEngine() async {
        let (vm, api, _) = make()
        api.responses["class.now"] = nowJSON("auto", key: "auto-\(UUID())")
        await vm.checkSoon()
        XCTAssertTrue(vm.isOpen)
        XCTAssertEqual(vm.mode, .online)
        XCTAssertNil(vm.ask)
        XCTAssertEqual(params(api, "class.reach").count, 1)
        XCTAssertTrue(params(api, "class.reach")[0].contains("\"suggest\":\"auto\""))
        let start = params(api, "class.start")
        XCTAssertEqual(start.count, 1)
        XCTAssertTrue(start[0].contains("Prashant Class") && start[0].contains("Physics") && start[0].contains("online"))
    }

    func testAskSuggestShowsTheCardInTheRoomWithoutALinkAndYesStartsIt() async {
        let (vm, api, _) = make()
        api.responses["class.now"] = nowJSON("ask", key: "ask-\(UUID())", online: false)
        await vm.checkSoon()
        XCTAssertFalse(vm.isOpen)
        XCTAssertEqual(vm.ask?.title, "Prashant Class")
        XCTAssertEqual(vm.ask?.online, false)
        await vm.takeNotes()
        XCTAssertEqual(vm.mode, .room)
        XCTAssertTrue(vm.isOpen)
    }

    func testNoneAndARepeatedEventDoNothing() async {
        let (vm, api, _) = make()
        api.responses["class.now"] = nowJSON("none", key: "none-\(UUID())")
        await vm.checkSoon()
        XCTAssertTrue(params(api, "class.reach").isEmpty && !vm.isOpen && vm.ask == nil)
        let key = "rep-\(UUID())"
        api.responses["class.now"] = nowJSON("ask", key: key)
        await vm.checkSoon()
        vm.skipAsk()
        await vm.checkSoon()
        XCTAssertNil(vm.ask)
        XCTAssertEqual(params(api, "class.reach").count, 1)
    }

    // MARK: started by the engine (the `class_start` move: "start my class")

    func testEngineStartRecordsLikeTheAskCardAndClosesTheCard() async {
        let (vm, api, _) = make()
        vm.showAsk(.init(title: "English Class", subject: "English", startsIn: 60))
        let r = await vm.startFromEngine(title: "English Class", subject: "English", online: true, key: "eng-\(UUID())")
        XCTAssertEqual(r, .started)
        XCTAssertTrue(vm.isOpen); XCTAssertNil(vm.ask); XCTAssertEqual(vm.mode, .online)
        let start = params(api, "class.start")
        XCTAssertEqual(start.count, 1)
        XCTAssertTrue(start[0].contains("English Class") && start[0].contains("English") && start[0].contains("online"), start[0])
    }

    func testEngineStartWhileARecordingRunsDoesNotStartASecondOne() async {
        let (vm, api, _) = make()
        await vm.start(subject: "Chem")
        let r = await vm.startFromEngine(title: "English Class", subject: "English", online: false, key: "")
        XCTAssertEqual(r, .already)
        XCTAssertEqual(params(api, "class.start").count, 1)
    }

    func testEngineStartWithTheMicOffReportsWhyAndNothingIsRecording() async {
        let (vm, _, _) = make()
        vm.listen = { _, _ in false }
        let r = await vm.startFromEngine(title: "English Class", subject: "English", online: false, key: "")
        XCTAssertEqual(r, .failed("Mic is off: Settings > Permissions"))
        XCTAssertFalse(vm.isOpen)
    }

    func testEngineStartInAVoiceTurnWaitsForHerReplyThenStarts() async throws {
        let (vm, api, _) = make()
        final class Busy: @unchecked Sendable { var on = true }
        let busy = Busy()
        vm.voiceBusy = { busy.on }
        vm.busyPoll = .milliseconds(10)
        let r = await vm.startFromEngine(title: "English Class", subject: "English", online: true, key: "")
        XCTAssertEqual(r, .queued)
        try await Task.sleep(for: .milliseconds(80))
        XCTAssertFalse(vm.isOpen)                               // her reply is still being spoken: the mic is not taken yet
        XCTAssertTrue(params(api, "class.start").isEmpty)
        busy.on = false
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertTrue(vm.isOpen)
        XCTAssertEqual(params(api, "class.start").count, 1)
    }

    func testTheReplyTheEngineGets() {
        XCTAssertEqual(ClassStartReply.make(.started).data["status"], .string("started"))
        XCTAssertEqual(ClassStartReply.make(.queued).data["status"], .string("queued"))
        let f = ClassStartReply.make(.failed("Mic is off"))
        XCTAssertTrue(f.ok)                                    // the wire answer is good; the outcome is inside
        XCTAssertEqual(f.data["ok"], .bool(false)); XCTAssertEqual(f.data["problem"], .string("Mic is off"))
    }

    func testTeacherEchoNeedsThreeWordsAndMostlyTheirs() {
        let teacher = VoiceIntent.words("a mole is the amount of substance containing avogadro's number of particles")
        XCTAssertTrue(VoiceIntent.isTeacherEcho("the amount of substance containing particles", of: teacher))
        XCTAssertFalse(VoiceIntent.isTeacherEcho("sorry is that per litre", of: teacher))      // his own question
        XCTAssertFalse(VoiceIntent.isTeacherEcho("what is a mole", of: teacher))               // reuses her terms, still him
        XCTAssertFalse(VoiceIntent.isTeacherEcho("a mole", of: teacher))                       // too short to judge
    }

    func testOnlineClassSendsNewSlidesAndInPersonNeverLooks() async {
        let (vm, api, clock) = make()
        api.responses["class.slide"] = #"{"id":1}"#
        var looks = 0
        vm.slideSource = { looks += 1; return "Le Chatelier principle equilibrium shifts" }
        vm.mode = .online
        await vm.start(subject: "Chem")
        clock.t += 30
        await vm.pollSlide()
        let p = params(api, "class.slide")
        XCTAssertEqual(p.count, 1)
        XCTAssertTrue(p[0].contains(#""t":30"#) && p[0].contains("Le Chatelier"), p[0])
        XCTAssertEqual(vm.slideCount, 1)
        await vm.end()

        let (room, api2, _) = make()
        var roomLooks = 0
        room.slideSource = { roomLooks += 1; return "should never be read" }
        await room.start(subject: "Chem")
        await room.pollSlide()
        XCTAssertEqual(roomLooks, 0)
        XCTAssertTrue(params(api2, "class.slide").isEmpty)
        XCTAssertEqual(looks, 1)
    }
}

final class SlideWatcherRuleTests: XCTestCase {
    typealias W = SlideWatcher.Win

    func testOnlyMeetingWindowsAreRead() {
        let chat = W(number: 1, owner: "Messages", title: "Mum", w: 900, h: 700)
        let misty = W(number: 2, owner: "Misty", title: "Misty", w: 900, h: 700)
        let mail = W(number: 3, owner: "Google Chrome", title: "Inbox - Gmail", w: 1400, h: 900)
        let zoom = W(number: 4, owner: "zoom.us", title: "Zoom Meeting", w: 1200, h: 800)
        let meet = W(number: 5, owner: "Google Chrome", title: "Meet - abc-defg-hij", w: 1400, h: 900)
        let tiny = W(number: 6, owner: "zoom.us", title: "Zoom", w: 200, h: 100)
        XCTAssertNil(SlideWatcher.pick([chat, misty, mail, tiny]))
        XCTAssertEqual(SlideWatcher.pick([chat, mail, zoom, meet])?.number, 4)       // front-most call wins
        XCTAssertEqual(SlideWatcher.pick([mail, meet])?.number, 5)
    }

    func testCallButtonsAreDroppedAndSlidesAreCompared() {
        XCTAssertEqual(SlideWatcher.clean(["Mute", "Participants", "Moles and molarity", "n = c x V", "5"]), "Moles and molarity n = c x V")
        let a = "Moles and molarity n equals c times V"
        XCTAssertFalse(SlideWatcher.isNewSlide(old: a, new: "Moles and molarity n equals c times V now"))   // same slide, cursor moved
        XCTAssertTrue(SlideWatcher.isNewSlide(old: a, new: "Titration practical: burette, pipette, indicator"))
        XCTAssertFalse(SlideWatcher.isNewSlide(old: a, new: "Mute"))                                         // too few words
        XCTAssertTrue(SlideWatcher.isNewSlide(old: "", new: "First slide of the lesson today"))
        XCTAssertEqual(SlideWatcher.distance([1, 2, 3], [1, 2, 3]), 0)
        XCTAssertEqual(SlideWatcher.distance([], [1]), 255)
    }
}

final class ClassCostTests: XCTestCase {
    func testLabelsStayReadableAtTinySizes() {
        XCTAssertEqual(ClassCost.label(0), "free")
        XCTAssertEqual(ClassCost.label(0.0006), "0.06¢")
        XCTAssertEqual(ClassCost.label(0.034), "3.4¢")
        XCTAssertEqual(ClassCost.label(1.2), "$1.20")
    }

    func testSessionDecodesCost() throws {
        let j = #"{"id":"cs_1","subject":"Chem","title":"Chem, 3 Nov","started_at":1,"status":"noted","his_notes":"","gaps":[],"pruned":0,"cost":0.0006}"#
        let s = try Wire.decoder().decode(ClassSession.self, from: Data(j.utf8))
        XCTAssertEqual(s.cost, 0.0006)
    }

    func testNotesDecodeLostAndNextStepAndOldNotesWithout() throws {
        let base = #""summary":"s","sections":[],"remember":[],"action_items":[],"gaps":[]"#
        let new = #"{\#(base),"lost":[{"text":"Doppler","seg":[2]}],"next_step":{"text":"Do page 112","seg":[3]}}"#
        let n = try Wire.decoder().decode(ClassNotes.self, from: Data(new.utf8))
        XCTAssertEqual(n.lost?.first?.text, "Doppler")
        XCTAssertEqual(n.nextStep?.seg, [3])
        let old = try Wire.decoder().decode(ClassNotes.self, from: Data("{\(base)}".utf8))
        XCTAssertNil(old.lost)
        XCTAssertNil(old.nextStep)
    }
}
