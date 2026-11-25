import XCTest
@testable import MistyCore

final class ContractDecodingTests: XCTestCase {
    func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try Wire.decoder().decode(T.self, from: fixtureData(name))
    }

    func testStatusFixtures() throws {
        let s = try decode(Status.self, "status")
        XCTAssertTrue(s.online)
        XCTAssertEqual(s.spend.capDaily, 0.2, accuracy: 1e-9)
        XCTAssertEqual(s.sources.first?.source, "folders")
        let b = try decode(Status.self, "status_building")
        XCTAssertEqual(b.build?.phase, "compile")
        XCTAssertEqual(b.sources.last?.health, "needs_fda")
        XCTAssertTrue(b.badge)
    }

    func testQueueKeysStayRawSnakeCase() throws {
        let raw = #"{"online":true,"paused":false,"queue":{"waiting_cap":2,"queued":5},"spend":{"today":0,"cap_daily":0.2,"build":null,"cap_build":3},"build":null,"sources":[],"badge":false,"health":{"stale_sources":["gmail"]}}"#
        let s = try Wire.decoder().decode(Status.self, from: Data(raw.utf8))
        XCTAssertEqual(s.queue["waiting_cap"], 2)
        XCTAssertEqual(s.health?["stale_sources"], .array([.string("gmail")]))
    }

    func testSuggestionsGraphPagePriorities() throws {
        let l = try decode(SuggestionList.self, "suggestion_list")
        XCTAssertEqual(l.suggestions.count, 2)
        XCTAssertEqual(l.suggestions[0].locator, "/x/it_c000000000000001")
        let g = try decode(GraphData.self, "graph")
        XCTAssertEqual(g.nodes.first?.type, "self")
        XCTAssertTrue(g.edges.contains { $0.kind == "belongs" })
        let area = g.nodes.first { $0.type == "area" }!
        XCTAssertEqual(area.more, 4)
        XCTAssertEqual(area.expanded, false)
        let group = g.nodes.first { $0.type == "group" }!
        XCTAssertEqual(group.members, 4)
        let inv = g.edges.first { $0.kind == "involves" }!
        XCTAssertGreaterThanOrEqual(inv.weight ?? 0, 2)
        let e = try decode(GraphData.self, "graph_expanded")
        XCTAssertEqual(e.nodes.first { $0.type == "area" }?.expanded, true)
        XCTAssertGreaterThan(e.nodes.count, g.nodes.count)
        let p = try decode(PageDetail.self, "page")
        XCTAssertEqual(p.fm["type"], .string("project"))
        XCTAssertEqual(p.sources.first?.title, "igem-notes.md")
        let pr = try decode(PrioritiesState.self, "priorities")
        XCTAssertEqual(pr.status, "approved")
        let d = try decode(PrioritiesDiff.self, "priorities_diff")
        XCTAssertEqual(Set(d.diff.map(\.op)), ["add", "remove"])
    }

    func testContextAndLookup() throws {
        let c = try decode(ContextPackDTO.self, "context")
        XCTAssertFalse(c.entities.isEmpty)
        XCTAssertTrue(c.entities[0].path.hasSuffix(".md"))
        XCTAssertNotNil(c.openNow)
        let l = try decode(LookupResult.self, "lookup")
        XCTAssertEqual(l.candidates.first?.name, "Aarav Shah")
        XCTAssertEqual(l.ambiguous, false)
    }

    func testQueryResults() throws {
        if case .results(let route, let hits) = try decode(QueryResult.self, "query_search") {
            XCTAssertEqual(route, "file"); XCTAssertEqual(hits.count, 1)
        } else { XCTFail("expected results") }
        if case .answer(_, let text, let cites) = try decode(QueryResult.self, "query_ask") {
            XCTAssertTrue(text.contains("iGEM")); XCTAssertEqual(cites, ["projects/igem", "areas/school"])
        } else { XCTFail("expected answer") }
        let pack = #"{"route":"action","context_pack":{"text":"about-isaac card","pages":[],"tokens":3}}"#
        if case .contextPack(let route, let p) = try Wire.decoder().decode(QueryResult.self, from: Data(pack.utf8)) {
            XCTAssertEqual(route, "action"); XCTAssertEqual(p.tokens, 3)
        } else { XCTFail("expected pack") }
    }

    func testEventFixtures() throws {
        if case .statusChanged(let s) = try RpcEvent.decode(fixtureData("event_status_changed")) { XCTAssertTrue(s.online) }
        else { XCTFail() }
        XCTAssertEqual(try RpcEvent.decode(fixtureData("event_suggestion_new")),
                       .suggestionNew(id: "sg_00000000000001", kind: "reply", urgency: "now"))
        if case .buildProgress(let b) = try RpcEvent.decode(fixtureData("event_build_progress")) { XCTAssertEqual(b.phase, "compile") }
        else { XCTFail() }
        XCTAssertEqual(try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"future.thing","params":{}}"#.utf8)),
                       .unknown("future.thing"))
    }

    func testEveryContractFixtureIsKnown() throws {
        let files = try FileManager.default.contentsOfDirectory(atPath: contractDir().path).filter { $0.hasSuffix(".json") }
        let known: Set<String> = ["status", "status_building", "suggestion_list", "graph", "page", "priorities",
                                  "priorities_diff", "query_search", "query_ask", "event_status_changed",
                                  "event_suggestion_new", "event_build_progress", "error", "graph_expanded", "context",
                                  "lookup"]
        XCTAssertEqual(Set(files.map { String($0.dropLast(5)) }), known, "new contract file needs a decoding test")
    }
}
