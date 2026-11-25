import XCTest
@testable import MistyCore

final class RpcClientTests: XCTestCase {
    func makeClient() async throws -> (RpcClient, FakeTransport) {
        let t = FakeTransport()
        let c = RpcClient(socketPath: URL(fileURLWithPath: "/x"), transport: t)
        try await c.connect()
        return (c, t)
    }

    func testRequestResponseCorrelationOutOfOrder() async throws {
        let (c, t) = try await makeClient()
        async let a: Status = c.call("status.get", Empty())
        async let b: SuggestionList = c.call("suggestions.list", ["status": "new"])
        await t.waitForSent(2)
        // ids are assigned in call order, whichever child task ran first: find them from what was sent
        var ids: [String: Int] = [:]
        for d in t.sent {
            let o = try JSONSerialization.jsonObject(with: d) as! [String: Any]
            ids[o["method"] as! String] = o["id"] as? Int
        }
        let list = try fixtureText("suggestion_list"), st = try fixtureText("status")
        t.reply(to: ids["suggestions.list"]!, resultJSON: list)       // answer the later request first
        t.reply(to: ids["status.get"]!, resultJSON: st)
        let (s, l) = try await (a, b)
        XCTAssertEqual(l.suggestions.count, 2)
        XCTAssertTrue(s.online)
        let first = String(data: t.sent[0], encoding: .utf8)!
        XCTAssertTrue(first.contains("\"method\":\"status.get\"") && first.hasSuffix("\n"))
    }

    func testTwoMessagesInOneReadAndEventsAreDelivered() async throws {
        let (c, t) = try await makeClient()
        let status = try fixtureText("status").replacingOccurrences(of: "\n", with: "")
        async let r: Status = c.call("status.get", Empty())
        await t.waitForSent(1)
        // an event and the reply arrive back to back
        t.feedString(#"{"jsonrpc":"2.0","method":"suggestion.new","params":{"id":"sg_1","kind":"task","urgency":"now"}}"#)
        t.reply(to: 1, resultJSON: status)
        _ = try await r
        var it = c.events.makeAsyncIterator()
        let ev = await it.next()
        XCTAssertEqual(ev, .suggestionNew(id: "sg_1", kind: "task", urgency: "now"))
    }

    func testServerErrorCarriesCode() async throws {
        let (c, t) = try await makeClient()
        async let r: Status = c.call("status.get", Empty())
        await t.waitForSent(1)
        t.replyError(to: 1, message: "KEY_INVALID")
        do { _ = try await r; XCTFail("expected error") }
        catch { XCTAssertEqual(error as? RpcError, .server(code: "KEY_INVALID")) }
    }

    func testTimeoutFailsTheCall() async throws {
        let (c, _) = try await makeClient()
        do { let _: Status = try await c.call("status.get", Empty(), timeout: .milliseconds(80)); XCTFail() }
        catch { XCTAssertEqual(error as? RpcError, .timeout) }
    }

    func testDisconnectFailsPendingCalls() async throws {
        let (c, t) = try await makeClient()
        async let r: Status = c.call("status.get", Empty())
        await t.waitForSent(1)
        t.drop()
        do { _ = try await r; XCTFail() }
        catch { XCTAssertEqual(error as? RpcError, .disconnected) }
    }

    func testParamsAreSnakeCased() async throws {
        let (c, t) = try await makeClient()
        struct P: Encodable, Sendable { var acceptedLineIds: [String] }
        async let r: JSONValue = c.call("priorities.apply", P(acceptedLineIds: ["d_1"]))
        await t.waitForSent(1)
        XCTAssertTrue(String(data: t.sent[0], encoding: .utf8)!.contains("accepted_line_ids"))
        t.reply(to: 1, resultJSON: #"{"ok":true}"#)
        _ = try await r
    }
}
