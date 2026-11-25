import XCTest
@testable import MistyCore

private func status(online: Bool = true, paused: Bool = false, queue: [String: Int] = [:],
                    sources: [SourceHealth] = [], build: BuildProgress? = nil, today: Double = 0.04) -> Status {
    Status(online: online, paused: paused, queue: queue,
           spend: SpendInfo(today: today, capDaily: 0.20, build: build == nil ? nil : 0.31, capBuild: 3.0),
           build: build, sources: sources, badge: false, health: nil)
}

final class StripModelTests: XCTestCase {
    func testHealthyStrip() {
        let m = StripModel.make(status: status(queue: ["queued": 1200, "running": 4]), engine: .running)
        XCTAssertEqual(m.dot, .green)
        XCTAssertEqual(m.text, "1,204 queued")
        XCTAssertEqual(m.spend, "$0.04 / $0.20")
        XCTAssertTrue(m.banners.isEmpty)
    }

    func testBuildLineAndBuildSpend() {
        let b = BuildProgress(id: "bd_1", phase: "compile", done: 312, total: 1870, failed: 0, spentUsd: 0.31, pausedBy: nil)
        let m = StripModel.make(status: status(build: b), engine: .running)
        XCTAssertEqual(m.build, "Compiling 312 / 1,870")
        XCTAssertEqual(m.spend, "$0.31 / $3.00")
    }

    func testSharedProblemsShowOneBanner() {
        let m = StripModel.make(
            status: status(sources: [SourceHealth(source: "imessage", health: "needs_fda", lastOkAt: nil),
                                     SourceHealth(source: "whatsapp", health: "needs_fda", lastOkAt: nil),
                                     SourceHealth(source: "gmail", health: "needs_auth", lastOkAt: nil),
                                     SourceHealth(source: "gcal", health: "needs_auth", lastOkAt: nil)]),
            engine: .running)
        XCTAssertEqual(m.banners.filter { $0.action == .openFullDiskAccess }.count, 1)
        XCTAssertEqual(m.banners.filter { $0.action == .reconnectGoogle }.count, 1)
    }

    func testEveryBanner() {
        let m = StripModel.make(
            status: status(online: false, queue: ["waiting_cap": 3, "failed": 2],
                           sources: [SourceHealth(source: "imessage", health: "needs_fda", lastOkAt: nil),
                                     SourceHealth(source: "gmail", health: "needs_auth", lastOkAt: nil),
                                     SourceHealth(source: "whatsapp", health: "needs_export", lastOkAt: nil)],
                           today: 0.2),
            engine: .crashLoop)
        let texts = Set(m.banners.map(\.text))
        XCTAssertTrue(texts.contains("Offline: queued, will resume"))
        XCTAssertTrue(texts.contains("EDITH needs Full Disk Access"))
        XCTAssertTrue(texts.contains("Reconnect Google"))
        XCTAssertTrue(texts.contains("WhatsApp changed: export a chat"))
        XCTAssertTrue(texts.contains("Cap hit: $0.20 spent today. Raise it or wait until midnight."))
        XCTAssertTrue(texts.contains("2 failed"))
        XCTAssertTrue(texts.contains("Engine keeps stopping: open logs"))
        XCTAssertEqual(m.dot, .red)
        XCTAssertEqual(m.banners.first { $0.id == "cap" }?.action, .raiseCap)
        XCTAssertEqual(m.banners.first { $0.id == "failed" }?.action, .retryFailed)
    }

    func testCapBannerDuringBuildNamesTheBuildBudget() {
        let b = BuildProgress(id: "bd", phase: "resolve", done: 10, total: 100, failed: 0, spentUsd: 0.31, pausedBy: "cap")
        let m = StripModel.make(status: status(queue: ["waiting_cap": 5], build: b), engine: .running)
        XCTAssertEqual(m.banners.first { $0.id == "cap" }?.text, "Build budget used: $0.31 of $3.00. Raise it to keep going.")
        XCTAssertEqual(m.banners.first { $0.id == "cap" }?.action, .raiseCap)
    }

    func testPausedIsAmberAndNoStatusMeansEngineOff() {
        XCTAssertEqual(StripModel.make(status: status(paused: true), engine: .running).dot, .amber)
        XCTAssertEqual(StripModel.make(status: status(paused: true, queue: ["queued": 877]), engine: .running).text,
                       "Paused · 877 waiting")
        XCTAssertEqual(StripModel.make(status: nil, engine: .stopped).text, "Engine off")
    }
}

final class SetupFlowTests: XCTestCase {
    func testOpenRouterNeedsOkAndZdr() {
        var s = SetupState()
        XCTAssertFalse(SetupFlow.canAdvance(from: .openrouter, s))
        s.openrouterOk = true
        XCTAssertFalse(SetupFlow.canAdvance(from: .openrouter, s))
        s.zdr = true
        XCTAssertTrue(SetupFlow.canAdvance(from: .openrouter, s))
    }

    func testFdaGrantedOrSkippedChats() {
        var s = SetupState()
        XCTAssertFalse(SetupFlow.canAdvance(from: .fda, s))
        s.skipChats = true
        XCTAssertTrue(SetupFlow.canAdvance(from: .fda, s))
    }

    func testGoogleRefusesOtherAccountsButMaySkip() {
        var s = SetupState()
        s.googleEmail = "other@example.com"
        XCTAssertFalse(SetupFlow.canAdvance(from: .google, s))
        s.googleEmail = "you@example.com"
        XCTAssertTrue(SetupFlow.canAdvance(from: .google, s))
        s = SetupState(); s.googleSkipped = true
        XCTAssertTrue(SetupFlow.canAdvance(from: .google, s))
    }

    func testBuildBlockedWhenEstimateOverCap() {
        var s = SetupState()
        s.estimateShown = true; s.estimateTotal = 4.2
        XCTAssertFalse(SetupFlow.canAdvance(from: .build, s))
        XCTAssertEqual(SetupFlow.overCapMessage(s), "Estimated $4.20 is over your $3 cap")
        s.estimateTotal = 1.9
        XCTAssertTrue(SetupFlow.canAdvance(from: .build, s))
        s.estimateShown = false
        XCTAssertFalse(SetupFlow.canAdvance(from: .build, s))
    }

    func testFdaPollingStopsOnGrant() async {
        actor Counter { var n = 0; func hit() -> Int { n += 1; return n } }
        let c = Counter()
        let granted = await SetupFlow.pollUntilGranted(interval: 0.01, check: { await c.hit() >= 3 },
                                                       sleep: { _ in })
        XCTAssertTrue(granted)
        let n = await c.hit()
        XCTAssertEqual(n, 4)                          // exactly 3 polls before, none after the grant
    }
}

final class GraphLayoutTests: XCTestCase {
    func graph(areas: Int = 4, entities: Int = 3) -> GraphData {
        var nodes = [GraphNode(id: "me", label: "Isaac", type: "self", area: nil, importance: 5, glow: 0, lastDate: nil)]
        for i in 0..<areas {
            nodes.append(GraphNode(id: "areas/a\(i)", label: "A\(i)", type: "area", area: nil, importance: 5 - i % 3, glow: 0, lastDate: nil))
        }
        for i in 0..<entities {
            nodes.append(GraphNode(id: "projects/p\(i)", label: "P\(i)", type: "project", area: i == 0 ? "a1" : nil,
                                   importance: 1 + i, glow: 0.5, lastDate: nil))
        }
        return GraphData(nodes: nodes, edges: [GraphEdge(source: "projects/p0", target: "areas/a1", kind: "belongs")])
    }

    func testSelfFixedAtOriginAndAreasOnRing() {
        let g = GraphLayout.prepare(graph())
        let me = g.nodes.first { $0.type == "self" }!
        XCTAssertTrue(me.fixed); XCTAssertEqual(me.x, 0); XCTAssertEqual(me.y, 0)
        for a in g.nodes where a.type == "area" {
            XCTAssertEqual(hypot(a.x, a.y), 240, accuracy: 0.01)
            XCTAssertTrue(a.fixed)
        }
    }

    func testEntityGetsASlotOnRingTwoInsideItsAreasSectorAndOtherSitsInTheGap() {
        let g = GraphLayout.prepare(graph())
        let area = g.nodes.first { $0.id == "areas/a1" }!
        let p0 = g.nodes.first { $0.id == "projects/p0" }!
        XCTAssertEqual(hypot(p0.areaX, p0.areaY), GraphLayout.ringTwoRadius, accuracy: 0.01)
        var d = abs(atan2(p0.areaY, p0.areaX) - atan2(area.y, area.x))
        if d > .pi { d = 2 * .pi - d }
        XCTAssertLessThanOrEqual(d, .pi / 4 + 1e-9)                      // inside the sector of 4 areas
        let p1 = g.nodes.first { $0.id == "projects/p1" }!
        XCTAssertEqual(hypot(p1.areaX, p1.areaY), 240, accuracy: 0.01)
        XCTAssertFalse(g.nodes.filter { $0.type == "area" }.contains { abs($0.x - p1.areaX) < 1 && abs($0.y - p1.areaY) < 1 })
    }

    func testAnExpandedAreasEntitiesStayInTheirOwnSector() {
        var nodes = graph(areas: 4, entities: 0).nodes
        for i in 0..<12 {
            nodes.append(GraphNode(id: "topics/t\(i)", label: "T\(i)", type: "topic", area: "a2", importance: 3,
                                   glow: 0, lastDate: nil))
        }
        let g = GraphLayout.prepare(GraphData(nodes: nodes, edges: []))
        let area = g.nodes.first { $0.id == "areas/a2" }!
        let mine = g.nodes.filter { $0.area == "a2" && $0.type == "topic" }
        XCTAssertEqual(mine.count, 12)
        let base = atan2(area.y, area.x)
        for n in mine {
            XCTAssertEqual(hypot(n.areaX, n.areaY), GraphLayout.ringTwoRadius, accuracy: 0.01)
            var d = abs(atan2(n.areaY, n.areaX) - base)
            if d > .pi { d = 2 * .pi - d }
            XCTAssertLessThanOrEqual(d, .pi / 4 + 1e-9)
        }
        XCTAssertEqual(Set(mine.map { "\($0.areaX.rounded())/\($0.areaY.rounded())" }).count, 12)   // no two share a slot
    }

    func testTogglingAnAreaTwiceReturnsToTheCollapsedSet() {
        var s = GraphViewState()
        XCTAssertTrue(s.expanded.isEmpty)
        s.toggle("areas/a1")
        XCTAssertEqual(s.params.expanded, ["areas/a1"])
        s.toggle("areas/a2")
        s.toggle("areas/a1")
        XCTAssertEqual(s.params.expanded, ["areas/a2"])
        s.toggle("areas/a2")
        XCTAssertTrue(s.expanded.isEmpty)
        s.focus("projects/p0")
        XCTAssertEqual(s.params.focus, "projects/p0")
        s.search("janaki")
        XCTAssertEqual(s.params.q, "janaki")
        s.search("  ")
        XCTAssertNil(s.params.q)
    }

    func testGroupNodesSitInTheGapOnTheOuterRingAndKeepTheirMemberCount() {
        var nodes = graph(areas: 3, entities: 0).nodes
        for i in 0..<3 {
            nodes.append(GraphNode(id: "group/whatsapp:c\(i)", label: "Study group \(i)", type: "group", area: nil, importance: 2,
                                   glow: 0, lastDate: nil, sources: 0, more: nil, expanded: nil, members: 4))
        }
        let g = GraphLayout.prepare(GraphData(nodes: nodes, edges: []))
        let groups = g.nodes.filter { $0.type == "group" }
        XCTAssertEqual(groups.count, 3)
        XCTAssertEqual(groups[0].members, 4)
        for grp in groups { XCTAssertEqual(hypot(grp.areaX, grp.areaY), GraphLayout.ringTwoRadius, accuracy: 0.01) }
        for (i, a) in groups.enumerated() {                       // squares never sit on top of each other
            for b in groups[(i + 1)...] { XCTAssertGreaterThan(hypot(a.areaX - b.areaX, a.areaY - b.areaY), 60) }
        }
    }

    func testRadiusFromImportanceAndDeterministicStart() {
        let a = GraphLayout.prepare(graph()), b = GraphLayout.prepare(graph())
        XCTAssertEqual(a, b)
        XCTAssertEqual(a.nodes.first { $0.id == "projects/p2" }!.radius, 4 + 3 * 3)
    }

    func testNodeCapDropsLowestImportanceFirst() {
        var nodes = graph(areas: 3, entities: 0).nodes
        for i in 0..<450 {
            nodes.append(GraphNode(id: "topics/t\(i)", label: "T\(i)", type: "topic", area: nil,
                                   importance: i < 100 ? 1 : 3, glow: 0, lastDate: nil))
        }
        let g = GraphLayout.prepare(GraphData(nodes: nodes, edges: []))
        XCTAssertEqual(g.nodes.count, 400)
        XCTAssertEqual(g.nodes.filter { $0.type == "area" }.count, 3)
        XCTAssertFalse(g.nodes.contains { $0.id == "topics/t0" })
        XCTAssertTrue(g.nodes.contains { $0.id == "topics/t449" })
    }
}

@MainActor
final class ViewModelTests: XCTestCase {
    func suggestion(_ id: String, at: Double, locator: String? = nil) -> Suggestion {
        Suggestion(id: id, itemId: "it_1", kind: "task", text: "t", urgency: "now", status: "new", snoozeUntil: nil,
                   sender: nil, createdAt: at, updatedAt: at, locator: locator, title: "T", source: "gmail")
    }

    func testOptimisticActAndRollbackOnError() async throws {
        let api = FakeAPI()
        api.responses["suggestions.list"] = try fixtureText("suggestion_list")
        let vm = SuggestionsVM(api: api)
        await vm.load()
        XCTAssertEqual(vm.cards.count, 2)
        await vm.act(vm.cards[0], .done)
        XCTAssertEqual(vm.cards.count, 1)
        api.failing = ["suggestions.act"]
        let card = vm.cards[0]
        await vm.act(card, .snooze(.tonight))
        XCTAssertEqual(vm.cards.map(\.id), [card.id])                 // rolled back
        XCTAssertNotNil(vm.lastError)
        XCTAssertTrue(api.calls.last!.params.contains("\"until\":\"tonight\""))
    }

    func testNewEventInsertsNewestFirst() {
        let vm = SuggestionsVM(api: FakeAPI())
        vm.handleNew(suggestion("a", at: 10))
        vm.handleNew(suggestion("b", at: 20))
        vm.handleNew(suggestion("a", at: 10))
        XCTAssertEqual(vm.cards.map(\.id), ["b", "a"])
    }

    func testSourceTargets() {
        let vm = SuggestionsVM(api: FakeAPI(), gmailAccountIndex: 1)
        XCTAssertEqual(vm.target(for: suggestion("a", at: 1, locator: "/Users/i/doc.pdf")), .file("/Users/i/doc.pdf"))
        XCTAssertEqual(vm.target(for: suggestion("a", at: 1, locator: "gmail:thread:18c2f")),
                       .url("https://mail.google.com/mail/u/1/#all/18c2f"))
        if case .hint = vm.target(for: suggestion("a", at: 1, locator: "imessage:chat1:2026-W42")) {} else { XCTFail() }
        XCTAssertEqual(vm.target(for: suggestion("a", at: 1, locator: nil)), .none)
    }

    func testPrioritiesAcceptAllNonePartialPayload() async throws {
        let api = FakeAPI()
        api.responses["priorities.get"] = try fixtureText("priorities")
        api.responses["priorities.diff"] = try fixtureText("priorities_diff")
        let vm = PrioritiesVM(api: api)
        await vm.load()
        XCTAssertTrue(vm.hasChanges)
        vm.acceptAll()
        XCTAssertEqual(vm.accepted.count, 2)
        vm.acceptNone()
        vm.toggle("d_c75de5")
        await vm.apply()
        let call = api.calls.first { $0.method == "priorities.apply" }!
        XCTAssertEqual(call.params, #"{"accepted_line_ids":["d_c75de5"]}"#)
    }

    func testEmptyDiffMeansNoChanges() async {
        let api = FakeAPI()
        api.responses["priorities.get"] = #"{"status":"approved","lines":[]}"#
        api.responses["priorities.diff"] = #"{"diff":[]}"#
        let vm = PrioritiesVM(api: api)
        await vm.load()
        XCTAssertFalse(vm.hasChanges)
    }
}

final class ChatPickerModelTests: XCTestCase {
    private let chats = [
        ChatInfoDTO(id: "111@s.whatsapp.net", name: "Arjun Rao", lastMessageAt: "2026-10-13T10:00:00Z", count: 40),
        ChatInfoDTO(id: "222@g.us", name: "NOI Training Group", lastMessageAt: "2026-10-12T10:00:00Z", count: 900),
        ChatInfoDTO(id: "333@g.us", name: "Class 4B parents", lastMessageAt: "2026-09-01T10:00:00Z", count: 12),
        ChatInfoDTO(id: "444@s.whatsapp.net", name: "+65 9123 4567", lastMessageAt: nil, count: 3),
    ]

    func testSearchIsCaseInsensitiveAndMatchesNames() {
        var m = ChatPickerModel(chats: chats, selected: [])
        m.query = "noi"
        XCTAssertEqual(m.visible.map(\.id), ["222@g.us"])
        m.query = "  rao "
        XCTAssertEqual(m.visible.map(\.id), ["111@s.whatsapp.net"])
        m.query = "9123"
        XCTAssertEqual(m.visible.map(\.id), ["444@s.whatsapp.net"])
    }

    func testGroupsOnlyFilter() {
        var m = ChatPickerModel(chats: chats, selected: [])
        m.groupsOnly = true
        XCTAssertEqual(m.visible.map(\.id), ["222@g.us", "333@g.us"])
        m.query = "parents"
        XCTAssertEqual(m.visible.map(\.id), ["333@g.us"])
    }

    func testSelectAndClearOnlyTouchWhatIsVisible() {
        var m = ChatPickerModel(chats: chats, selected: ["111@s.whatsapp.net"])
        m.groupsOnly = true
        m.selectVisible()
        XCTAssertEqual(m.selected, ["111@s.whatsapp.net", "222@g.us", "333@g.us"])
        m.query = "noi"
        m.clearVisible()
        XCTAssertEqual(m.selected, ["111@s.whatsapp.net", "333@g.us"])
        m.toggle("444@s.whatsapp.net")
        XCTAssertTrue(m.selected.contains("444@s.whatsapp.net"))
        m.toggle("444@s.whatsapp.net")
        XCTAssertFalse(m.selected.contains("444@s.whatsapp.net"))
    }

    func testExcludedIsEverythingNotSelected() {
        let m = ChatPickerModel(chats: chats, selected: ["111@s.whatsapp.net", "222@g.us"])
        XCTAssertEqual(m.excluded, ["333@g.us", "444@s.whatsapp.net"])
        let all = ChatPickerModel(chats: chats, excluded: ["333@g.us"])
        XCTAssertEqual(all.selected.count, 3)
        XCTAssertFalse(all.selected.contains("333@g.us"))
    }

    func testSummaryAndWeekCountHint() {
        var m = ChatPickerModel(chats: chats, selected: ["111@s.whatsapp.net"])
        XCTAssertEqual(m.summary, "1 of 4 chats selected")
        m.query = "zzz"
        XCTAssertEqual(m.visible.count, 0)
        XCTAssertEqual(m.summary, "1 of 4 chats selected (0 shown)")
    }

    func testThePrivateStateRoundTripsAndIsKeyedBySourceAndChat() {
        var m = ChatPickerModel(chats: chats, selected: [])
        XCTAssertTrue(m.privateIds.isEmpty)
        m.setPrivate(["whatsapp:222@g.us", "imessage:iMessage;-;+6591234567"])
        XCTAssertTrue(m.isPrivate("222@g.us", source: "whatsapp"))
        XCTAssertFalse(m.isPrivate("222@g.us", source: "imessage"))
        m.togglePrivate("111@s.whatsapp.net", source: "whatsapp")
        XCTAssertEqual(m.privateIds, ["whatsapp:222@g.us", "whatsapp:111@s.whatsapp.net",
                                      "imessage:iMessage;-;+6591234567"])
        m.togglePrivate("222@g.us", source: "whatsapp")
        XCTAssertFalse(m.isPrivate("222@g.us", source: "whatsapp"))
        let dto = PrivateChatList(private: [PrivateChat(source: "whatsapp", chatId: "111@s.whatsapp.net")])
        m.setPrivate(Set(dto.private.map { "\($0.source):\($0.chatId)" }))
        XCTAssertEqual(m.privateIds, ["whatsapp:111@s.whatsapp.net"])
    }

    func testIDsThatAreNoLongerListedAreKeptOnSave() {
        // a picked chat can disappear from the list (deleted in the app); saving must not silently forget it
        let m = ChatPickerModel(chats: chats, selected: ["111@s.whatsapp.net", "gone@g.us"])
        XCTAssertEqual(m.selected.count, 2)
        XCTAssertEqual(m.excluded, ["222@g.us", "333@g.us", "444@s.whatsapp.net"])
    }
}
