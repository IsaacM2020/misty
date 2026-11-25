import XCTest
@testable import MistyCore

/// F5: ambient mode. The mic is on and she answers only to her name or inside the follow-up window; everything else is dropped
/// with no model call. Fake frames into the voice code, fake phrases, no sound, no model.
@MainActor
final class AmbientTests: XCTestCase {
    typealias R = VoiceIntent

    // ---- the name rules ---------------------------------------------------------------------------------------------

    /// 200 made-up phrases said to someone else or to nobody. They hold "kit", "missy", "misty weather" and "Edith Piaf".
    static let notForHer: [String] = {
        var out: [String] = []
        let kitPlaces = ["on the shelf", "in the car", "under the sink", "by the door", "in the bathroom"]
        for p in kitPlaces {
            out.append("Pass me the first aid kit")                         // the name is the LAST word
            out.append("Where did you put the kit \(p)")
            out.append("The repair kit is \(p)")
            out.append("Kit, dinner is ready")                              // the old name, now a person or a pet
        }
        let missy = ["Missy, dinner is ready", "Missy come here", "Missy the dog is barking", "Is that you, Missy", "Thank you Missy",
                     "Missy! Not on the sofa", "Where is Missy", "Missy wants a walk"]
        out += missy + missy.map { $0 + " please" } + missy.map { "Okay " + $0 }
        let weather = ["Misty weather again today", "It is such a misty weather", "Misty weather is coming on Sunday", "I love misty weather",
                       "Misty mornings are the best", "Misty sky over the hills", "Misty mountains tonight", "The hills look misty",
                       "The whole valley was misty", "Going out in the misty rain", "It is getting misty outside", "Everything feels so misty"]
        out += weather + weather.map { "Honestly " + $0.lowercased() } + weather.map { $0 + ", right?" } + weather.map { "So " + $0.lowercased() }
        let edith = ["Edith Piaf is playing", "I love Edith Piaf", "We listened to Edith Piaf all night", "Edith Piaf was a French singer",
                     "Put on some Edith Piaf", "Edith's birthday is next week", "Edith is coming for lunch", "Edith was late again",
                     "My aunt Edith is visiting", "I told Edith about the trip", "Ask Edith when she is free", "Did you see Edith yesterday"]
        out += edith + edith.map { $0 + " today" } + edith.map { "Yesterday " + $0.lowercased() } + edith.map { $0 + ", I think" }
        let plain = ["What time is dinner", "I think the bus is late", "Can you pass the salt", "That game was unreal",
                     "We need milk and eggs", "Turn the music down a bit", "Did you finish your homework", "It is raining again",
                     "I will call you back later", "Who left the door open", "That film was really long", "Let's order a pizza tonight",
                     "The match starts at seven", "Can you close the window", "I forgot my charger", "What are we having for lunch"]
        out += plain + plain.map { $0 + " today" } + plain.map { "Well, " + $0.lowercased() } + plain.map { $0 + " tonight" }
        precondition(out.count >= 200, "fixture is \(out.count)")
        return Array(out.prefix(200))
    }()

    static let forHer: [String] = {
        let asks = ["what is on my calendar today", "what time is it", "what is the weather like", "read me my mail",
                    "remind me to call mum at six", "how long until my next class"]
        var out: [String] = []
        for a in asks {
            out.append("Edith, \(a)")
            out.append("Misty, \(a)")
            out.append("Hey Edith, \(a)")
            out.append("hey misty \(a)")
            out.append("\(a.prefix(1).uppercased() + a.dropFirst()), Edith")
            out.append("\(a.prefix(1).uppercased() + a.dropFirst()) Misty?")
        }
        out += ["Edith", "Misty?", "Okay Edith", "Thanks Edith", "Hi Misty, are you there"]
        out += ["Edith what is two plus two", "Misty stop that", "Hey Edith can you check my mail", "Can you hear me Edith"]
        return Array(out.prefix(40))
    }()

    func testTheFixturesAreTheSizeTheCardSays() {
        XCTAssertEqual(Self.notForHer.count, 200)
        XCTAssertEqual(Self.forHer.count, 40)
        XCTAssertGreaterThanOrEqual(Self.notForHer.filter { $0.lowercased().contains("kit") }.count, 20)
        XCTAssertGreaterThanOrEqual(Self.notForHer.filter { $0.lowercased().contains("missy") }.count, 20)
        XCTAssertGreaterThanOrEqual(Self.notForHer.filter { $0.lowercased().contains("misty weather") }.count, 5)
        XCTAssertGreaterThanOrEqual(Self.notForHer.filter { $0.lowercased().contains("edith piaf") }.count, 5)
    }

    func testNoneOfTheTwoHundredNotForHerPhrasesWakesHerInAmbientMode() {
        let woke = Self.notForHer.filter { R.ambientName($0) }
        XCTAssertEqual(woke, [], "these woke her: \(woke)")
    }

    func testAtLeast38OfTheFortyAddressedLinesWakeHer() {
        let missed = Self.forHer.filter { !R.ambientName($0) }
        XCTAssertLessThanOrEqual(missed.count, 2, "missed: \(missed)")
    }

    func testKitAndMissyStillWakeHerForTapToTalkButNotInAmbientMode() {
        for w in ["Kit, what time is it", "Missy what time is it", "what time is it kit"] {
            XCTAssertTrue(R.namesHer(w), w)
            XCTAssertFalse(R.ambientName(w), w)
        }
    }

    func testTheNameCountsOnlyAtTheStartTheEndOrAfterHey() {
        XCTAssertTrue(R.ambientName("Edith what is the time"))
        XCTAssertTrue(R.ambientName("what is the time Edith"))
        XCTAssertTrue(R.ambientName("hey Edith what is the time"))
        XCTAssertFalse(R.ambientName("I asked Edith what the time is"))
        XCTAssertFalse(R.ambientName("what is the time"))
        XCTAssertFalse(R.ambientName(""))
    }

    func testARenamedAssistantWakesHerToo() {
        XCTAssertTrue(R.ambientName("Juno what is the time", extra: ["Juno"]))
        XCTAssertFalse(R.ambientName("Juno what is the time"))
    }

    // ---- turning it on and off by talking ----------------------------------------------------------------------------

    func testAmbientOnAndOffByTalking() {
        XCTAssertEqual(R.ambient("Edith, ambient on"), true)
        XCTAssertEqual(R.ambient("ambient mode off"), false)
        XCTAssertEqual(R.ambient("Edith, stop listening for my name"), false)
        XCTAssertEqual(R.ambient("Edith, start listening for my name"), true)
        XCTAssertNil(R.ambient("what is the time"))
        XCTAssertNil(R.ambient("I was listening to the radio"))
    }

    // ---- the route, one test per branch ------------------------------------------------------------------------------

    func choice(_ s: String, cls: Bool = false, merge: Bool = false, held: Bool = false, follow: Bool = false, mode: AmbientMode = .name) -> RouteChoice {
        R.routeChoice(s, classMode: cls, canMerge: merge, hasHeld: held, followUp: follow, ambient: mode)
    }

    func testClassModeIsSilentWhateverElseIsTrue() {
        XCTAssertEqual(choice("Edith what time is it", cls: true, merge: true, held: true, follow: true), .silent)
    }

    func testMoreWordsSoonAfterASendAreMerged() {
        XCTAssertEqual(choice("and tomorrow too", merge: true), .merge)
    }

    func testTheRestOfAHeldSentenceIsTheHeldRest() {
        XCTAssertEqual(choice("the dentist at four", held: true), .heldRest)
    }

    func testHerNameDelivers() {
        XCTAssertEqual(choice("Edith what time is it"), .deliver)
    }

    func testTheFollowUpWindowDeliversWithoutAName() {
        XCTAssertEqual(choice("and what about tomorrow", follow: true), .deliver)
    }

    func testAnythingElseIsDroppedWithNoModelCallInNameMode() {
        for s in Self.notForHer { XCTAssertEqual(choice(s), .drop, s) }
        XCTAssertEqual(choice("Missy dinner is ready"), .drop)
    }

    func testOffModeKeepsTheOldWaysAndAsksTheJudge() {
        XCTAssertEqual(choice("Kit what time is it", mode: .off), .deliver)        // the old names still wake her when ambient is off
        XCTAssertEqual(choice("dinner at eight", mode: .off), .judge)
        XCTAssertEqual(choice("dinner at eight", mode: .judge), .addressed)             // F7: the new judge, not the old live.hear
    }

    // ---- through the real voice code: no live.hear in name mode ------------------------------------------------------------

    func make() -> (VoiceVM, FakeAPI) {
        let api = FakeAPI()
        let v = VoiceVM(api: api, chat: ChatVM(api: api))
        v.speakReplies = false
        return (v, api)
    }

    func settle() async { try? await Task.sleep(for: .milliseconds(120)) }

    func testTwoHundredRoomPhrasesNeverReachTheJudgeOrTheChat() async {
        let (v, api) = make()
        v.testStartAmbient(.name)
        for s in Self.notForHer { v.testHeard(.final(s)) }
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "live.hear" }.count, 0)
        XCTAssertEqual(api.calls.filter { $0.method.hasPrefix("chat.") }.count, 0)
        XCTAssertEqual(v.ambientDrops, 200)
    }

    func testHerNameInAmbientModeStillReachesTheChat() async {
        let (v, api) = make()
        v.testStartAmbient(.name)
        v.testHeard(.final("Edith, what time is it"))
        await settle()
        XCTAssertTrue(api.calls.contains { $0.method == "chat.send" })
        XCTAssertEqual(api.calls.filter { $0.method == "live.hear" }.count, 0)
    }

    // ---- the dot lights up when her name is heard ----------------------------------------------------------------------------

    func testTheDotLightsUpAsSoonAsHerNameIsInAPartial() async {
        let (v, _) = make()
        v.testStartAmbient(.name)
        XCTAssertFalse(v.nameHeard)
        v.testHeard(.partial("dinner is at eight"))
        XCTAssertFalse(v.nameHeard)                                  // room talk never lights it
        v.testHeard(.partial("Misty"))
        XCTAssertTrue(v.nameHeard)
    }

    func testEdithAlsoLightsTheDotAndSendingItDimsIt() async {
        let (v, _) = make()
        v.testStartAmbient(.name)
        v.testHeard(.partial("Edith what time"))
        XCTAssertTrue(v.nameHeard)
        v.testHeard(.final("Edith what time is it"))                // delivered to her: the glow has done its job
        await settle()
        XCTAssertFalse(v.nameHeard)
    }

    func testTheWordMistyAboutTheWeatherDoesNotLightTheDot() async {
        let (v, _) = make()
        v.testStartAmbient(.name)
        v.testHeard(.final("the hills look misty"))                 // about the weather, not a call
        XCTAssertFalse(v.nameHeard)
    }

    func testOffModeNeverGlows() async {
        let (v, _) = make()
        v.testStartAmbient(.off)
        v.testHeard(.partial("Misty"))
        XCTAssertFalse(v.nameHeard)
    }

    func testOffModeStillAsksTheJudgeAboutRoomTalk() async {
        let (v, api) = make()
        v.testStartAmbient(.off)
        v.testHeard(.final("dinner is at eight tonight"))
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "live.hear" }.count, 1)
    }

    func testTheFirstPartialThatHoldsHerNameWarmsTheCacheOnce() async {
        let (v, api) = make()
        v.testStartAmbient(.name)
        v.testHeard(.partial("what is the"))
        v.testHeard(.partial("Edith what is"))
        v.testHeard(.partial("Edith what is the time"))
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "chat.warm" }.count, 1)
    }

    func testRoomTalkWithoutHerNameNeverWarmsTheCache() async {
        let (v, api) = make()
        v.testStartAmbient(.name)
        v.testHeard(.partial("pass me the first aid kit"))
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "chat.warm" }.count, 0)
    }

    // ---- it turns itself off ---------------------------------------------------------------------------------------------

    func testAClassOpeningTurnsItOff() {
        let (v, _) = make()
        v.testStartAmbient(.name)
        v.classMode = true
        XCTAssertEqual(v.ambient, .off)
    }

    func testSleepTurnsItOff() {
        let (v, _) = make()
        v.testStartAmbient(.name)
        v.testWillSleep()
        XCTAssertEqual(v.ambient, .off)
        XCTAssertEqual(v.state, .idle)
    }

    func testACallTurnsItOffAndOnlyACall() {
        let (v, _) = make()
        v.testStartAmbient(.name)
        v.inCall = { nil }
        v.testWatchTick()
        XCTAssertEqual(v.ambient, .name)
        v.inCall = { "Zoom" }
        v.testWatchTick()
        XCTAssertEqual(v.ambient, .off)
    }

    func testTheKeyEndsIt() async {
        let (v, _) = make()
        v.testStartAmbient(.name)
        await v.tap()
        XCTAssertEqual(v.ambient, .off)
        XCTAssertEqual(v.state, .idle)
    }

    // ---- what the pill, the menu bar and the log show ------------------------------------------------------------------

    func testThePillSaysListeningForYourNameAndNeverShowsRoomTalk() {
        let (v, _) = make()
        v.testStartAmbient(.name)
        XCTAssertEqual(v.stateWord, "Listening for your name")
        v.testHeard(.partial("pass me the first aid kit"))
        XCTAssertFalse(v.line.lowercased().contains("kit"))
    }

    func testAmbientModeNames() {
        XCTAssertEqual(AmbientMode(word: "NAME"), .name)
        XCTAssertEqual(AmbientMode(word: "off"), .off)
        XCTAssertEqual(AmbientMode(word: "Judge"), .judge)      // F7: the engine only sends it once the pass file exists
        XCTAssertNil(AmbientMode(word: "loud"))
    }

    // ---- the setting tool's app side ------------------------------------------------------------------------------------

    func testTheSettingAppliesAndReportsWhatTheAppNowHolds() async {
        let t = FakeTarget()
        let r = await SettingApply.run(name: "ambient", value: .string("name"), on: t)
        XCTAssertTrue(r.ok); XCTAssertEqual(r.data["value"], .string("name")); XCTAssertEqual(t.ambientMode, "name")
        let o = await SettingApply.run(name: "ambient", value: .string("off"), on: t)
        XCTAssertEqual(o.data["value"], .string("off")); XCTAssertEqual(t.ambientMode, "off")
    }

    func testGarbageIsRefusedAndChangesNothing() async {
        let t = FakeTarget()
        for bad in ["loud", ""] {
            let r = await SettingApply.run(name: "ambient", value: .string(bad), on: t)
            XCTAssertFalse(r.ok, bad)
        }
        XCTAssertEqual(t.ambientMode, "off")
    }

    func testWithoutHisYesToTheConsentItStaysOffAndSaysSo() async {
        let t = FakeTarget()
        t.refuseAmbient = true
        let r = await SettingApply.run(name: "ambient", value: .string("name"), on: t)
        XCTAssertEqual(r.data["value"], .string("off"))
        XCTAssertEqual(t.ambientMode, "off")
    }

    // ---- F7: the judge mode (the engine decides; the app only routes) ---------------------------------------------------

    func testJudgeModeNamesAndFollowUpsStillDeliverWithoutAskingTheJudge() async {
        let (v, api) = make()
        v.testStartAmbient(.judge)
        v.testHeard(.final("Edith, what time is it"))
        await settle()
        XCTAssertTrue(api.calls.contains { $0.method == "chat.send" })
        XCTAssertEqual(api.calls.filter { $0.method == "addressed.judge" }.count, 0)
    }

    func testJudgeModeAsksTheNewJudgeAndDeliversOnlyForHer() async {
        let (v, api) = make()
        api.responses["addressed.judge"] = #"{"label":"for_her","why":"asks the time","answers":true}"#
        v.testStartAmbient(.judge)
        v.testHeard(.final("what time is it"))
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "addressed.judge" }.count, 1)
        XCTAssertEqual(api.calls.filter { $0.method == "live.hear" }.count, 0)             // never the old judge
        XCTAssertTrue(api.calls.contains { $0.method == "chat.send" })
    }

    func testJudgeModeDropsNotForHerUnsureFailureAndGarbage() async {
        for reply in [#"{"label":"not_for_her","why":"x","answers":false}"#, #"{"label":"unsure","why":"x","answers":false}"#,
                      #"{"label":"for_her","why":"x","answers":false}"#, "{}", #"{"label":"yes"}"#] {
            let (v, api) = make()
            api.responses["addressed.judge"] = reply
            v.testStartAmbient(.judge)
            v.testHeard(.final("dinner is at eight tonight"))
            await settle()
            XCTAssertFalse(api.calls.contains { $0.method == "chat.send" }, reply)
            XCTAssertEqual(v.ambientDrops, 1, reply)
        }
        let (v, api) = make()
        api.failing = ["addressed.judge"]
        v.testStartAmbient(.judge)
        v.testHeard(.final("dinner is at eight tonight"))
        await settle()
        XCTAssertFalse(api.calls.contains { $0.method == "chat.send" })
        XCTAssertEqual(v.ambientDrops, 1)
    }

    func testJudgeModeIsSilentInClassMode() async {
        let (v, api) = make()
        v.testStartAmbient(.judge)
        v.classMode = true                                                                  // a class is silent: no judge, no chat
        v.testHeard(.final("dinner is at eight tonight"))
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "addressed.judge" }.count, 0)
    }

    func testNameModeStillAsksNobodyAboutRoomTalk() async {
        let (v, api) = make()
        api.responses["addressed.judge"] = #"{"label":"for_her","why":"x","answers":true}"#
        v.testStartAmbient(.name)
        for s in Self.notForHer.prefix(50) { v.testHeard(.final(s)) }
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "addressed.judge" }.count, 0)
        XCTAssertEqual(api.calls.filter { $0.method == "chat.send" }.count, 0)
    }

    // ---- ambient voice: the room clue, the chosen mode kept, the key's pause ---------------------------------------------

    func testTheJudgeIsToldHowBusyTheRoomIs() async {
        let (v, api) = make()
        api.responses["addressed.judge"] = #"{"label":"not_for_her","why":"","usd":0,"answers":false}"#
        v.testStartAmbient(.judge)
        v.testHeard(.final("did you finish the homework already"))
        await settle()
        v.testHeard(.final("no I'll do it after dinner tonight"))
        await settle()
        let sent = api.calls.filter { $0.method == "addressed.judge" }.map(\.params)
        XCTAssertEqual(sent.count, 2)
        XCTAssertTrue(sent[0].contains("\"room\":0"), sent[0])
        XCTAssertTrue(sent[1].contains("\"room\":1"), sent[1])
    }

    func testTheRoomForgetsAfterThirtySeconds() {
        let (v, _) = make()
        v.testRoomHeard(Date().addingTimeInterval(-40))
        v.testRoomHeard(Date().addingTimeInterval(-5))
        XCTAssertEqual(v.roomCount(), 1)
    }

    func testHisChoiceIsKeptAndOnlyHeTurnsItOff() async {
        let saved = VoiceVM.wantedStore.string(forKey: VoiceVM.ambientWantedKey)
        defer { VoiceVM.wantedStore.set(saved, forKey: VoiceVM.ambientWantedKey) }
        let (v, _) = make()
        _ = await v.setAmbient(.off)
        XCTAssertEqual(VoiceVM.ambientWanted, .off)
        XCTAssertFalse(v.canResumeAmbient(consent: true, call: nil))
        VoiceVM.ambientWanted = .judge
        XCTAssertTrue(v.canResumeAmbient(consent: true, call: nil))      // off after a sleep, a class or a launch: it comes back
        XCTAssertFalse(v.canResumeAmbient(consent: false, call: nil))     // never without the live consent
        XCTAssertFalse(v.canResumeAmbient(consent: true, call: "zoom.us")) // never in a call
        v.classMode = true
        XCTAssertFalse(v.canResumeAmbient(consent: true, call: nil))      // never in a class
    }

    func testTheKeyPausesAmbientForTwoMinutes() async {
        let saved = VoiceVM.wantedStore.string(forKey: VoiceVM.ambientWantedKey)
        defer { VoiceVM.wantedStore.set(saved, forKey: VoiceVM.ambientWantedKey) }
        let (v, _) = make()
        VoiceVM.ambientWanted = .judge
        v.testStartAmbient(.judge)
        await v.tap()
        XCTAssertEqual(v.ambient, .off)
        XCTAssertFalse(v.canResumeAmbient(consent: true, call: nil))
        XCTAssertTrue(v.canResumeAmbient(now: Date().addingTimeInterval(VoiceVM.ambientPause + 1), consent: true, call: nil))
    }

    func testDictationIsTypedByTheEngineNotSentToTheChat() async {
        let (v, api) = make()
        api.responses["addressed.judge"] = #"{"label":"write","why":"","usd":0,"answers":false,"wrote":true,"app":"Mail"}"#
        v.testStartAmbient(.judge)
        v.testHeard(.final("thursday works for me see you then"))
        await settle()
        XCTAssertEqual(api.calls.filter { $0.method == "chat.send" }.count, 0)
        XCTAssertEqual(v.roomCount(), 0)                 // his own dictation is not the room talking
        XCTAssertEqual(v.ambientDrops, 0)
    }

    func testTheFrameSaysWhenTheCursorIsInATextBox() {
        let app = HandsAppInfo(bundleId: "com.apple.mail", pid: 9, name: "Mail")
        XCTAssertEqual(ContextFrame.make(app: app, reading: FocusReading(title: "Re: hi", typing: true)).json["typing"], .bool(true))
        XCTAssertNil(ContextFrame.make(app: app, reading: FocusReading(title: "Re: hi", secure: true, typing: true)).json["typing"])
        XCTAssertNil(ContextFrame.make(app: app, reading: FocusReading(title: "Inbox")).json["typing"])
    }

    func testTheLiveLogShowsEachStepAndKeepsOnlyTheLast200() async {
        let (v, api) = make()
        api.responses["addressed.judge"] = #"{"label":"write","why":"","text":"Thursday works for me.","usd":0,"answers":false,"wrote":true,"app":"Mail"}"#
        v.testStartAmbient(.judge)
        v.testHeard(.final("um thursday works for me"))
        await settle()
        let e = v.thoughts.entries.first
        XCTAssertEqual(e?.heard, "um thursday works for me")
        XCTAssertEqual(e?.verdict, .typed)
        XCTAssertTrue(e?.result.contains("Thursday works for me.") == true, e?.result ?? "")
        XCTAssertTrue(e?.steps.contains { $0.text.contains("asking the judge") } == true)
        let log = ThoughtLog()
        for i in 0..<250 { log.begin("phrase \(i)") }
        XCTAssertEqual(log.entries.count, ThoughtLog.keep)
        XCTAssertEqual(log.entries.first?.heard, "phrase 249")
    }

    func testNameOnlyDropsSayWhyInTheLog() async {
        let (v, _) = make()
        v.testStartAmbient(.name)
        v.testHeard(.final("what do you want for dinner tonight"))
        await settle()
        XCTAssertEqual(v.thoughts.entries.first?.verdict, .ignored)
        XCTAssertTrue(v.thoughts.entries.first?.result.contains("Her name only") == true)
    }
}
