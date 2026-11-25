import XCTest
@testable import MistyCore

@MainActor
final class ChatVMTests: XCTestCase {
    func testChatEventsDecode() throws {
        let d = try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"chat.delta","params":{"turn":"t1","text":"Open"}}"#.utf8))
        XCTAssertEqual(d, .chatDelta(turn: "t1", text: "Open"))
        let c = try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"chat.confirm","params":{"turn":"t1","id":"c_1","question":"Send it?"}}"#.utf8))
        XCTAssertEqual(c, .chatConfirm(turn: "t1", id: "c_1", question: "Send it?"))
        let e = try RpcEvent.decode(Data(#"{"jsonrpc":"2.0","method":"chat.done","params":{"turn":"t1"}}"#.utf8))
        XCTAssertEqual(e, .chatDone(turn: "t1"))
    }

    func testReplyKeepsStreamedTextAndFallsBackToTheFinalReply() async {
        let api = FakeAPI()
        api.responses["chat.send"] = #"{"reply":"Opened your essay.","actions":[],"usd":0.001}"#
        api.responses["facts.list"] = #"{"facts":[{"id":"f_1","key":"english task","text":"the essay","link":"https://x","link_title":"Essay","updated_at":1}]}"#
        let vm = ChatVM(api: api)
        await vm.send("do my english work")
        XCTAssertEqual(vm.messages.map(\.role), ["user", "assistant"])
        XCTAssertEqual(vm.messages[1].text, "Opened your essay.")          // nothing streamed: the final reply is used
        XCTAssertFalse(vm.messages[1].pending)
        XCTAssertEqual(vm.facts.first?.linkTitle, "Essay")
        let turn = vm.messages[1].id
        XCTAssertTrue(api.calls.contains { $0.method == "chat.send" && $0.params.contains(turn) })

        vm.delta(turn: turn, text: " More.")                               // a late delta lands on its own turn
        XCTAssertEqual(vm.messages[1].text, "Opened your essay. More.")
    }

    func testConfirmIsAnsweredOnce() async {
        let api = FakeAPI()
        let vm = ChatVM(api: api)
        api.responses["chat.send"] = #"{"reply":"ok"}"#
        await vm.send("email it")
        let turn = vm.messages[1].id
        vm.ask(turn: turn, id: "c_1", question: "Send it?")
        await vm.answer(turn: turn, yes: false)
        await vm.answer(turn: turn, yes: true)
        XCTAssertEqual(vm.messages[1].confirm?.answer, false)
        XCTAssertEqual(api.calls.filter { $0.method == "chat.answer" }.count, 1)
    }
}

/// The answer to chat.send and the streamed pieces travel separately; the last words must never be lost to that race.
final class ReplyTailTests: XCTestCase {
    func testWholeReplyStreamedMeansNothingMissing() {
        XCTAssertEqual(ReplyTail.missing(shown: "Physics at two.", reply: "Physics at two."), nil)
    }

    func testTheLastPieceStillOnItsWayIsFound() {
        XCTAssertEqual(ReplyTail.missing(shown: "You have physics at 2 and", reply: "You have physics at 2 and maths at 4."),
                       " maths at 4.")
    }

    func testNothingStreamedYetMeansTheWholeReply() {
        XCTAssertEqual(ReplyTail.missing(shown: "", reply: "Physics at two."), "Physics at two.")
    }

    func testAReplyThatDoesNotStartWithWhatWasShownIsNotSpliced() {
        XCTAssertNil(ReplyTail.missing(shown: "Something else entirely", reply: "Physics at two."))
    }

    func testSurroundingWhitespaceIsIgnored() {
        XCTAssertEqual(ReplyTail.missing(shown: "Let me check.\n", reply: "Let me check.\nPhysics at 2."), "\nPhysics at 2.")
    }
}

final class CostReportDecodingTests: XCTestCase {
    func testTheEnginesBreakdownDecodesIncludingOldRepliesWithoutRounds() throws {
        let json = #"""
        {"days":7,"today":0.0123,"window":0.5,
         "by_purpose":[{"label":"Chat replies","calls":3,"usd":0.0044,"models":["claude-haiku-5.5 x3"]}],
         "by_day":[{"day":"Sat 24 Oct","usd":0.0123}],
         "messages":[
          {"id":9,"at":"Sat 24 Oct 18:30","question":"what's on","reply":"Physics","usd":0.0031,
           "rounds":[{"in":5000,"cached":4000,"out":40,"usd":0.0015,"exact":true,"tools":["calendar"]}],
           "tools":["calendar"],"tool_usd":[{"tool":"do","usd":0.002}]},
          {"id":4,"at":"Fri 23 Oct 09:00","question":"old","reply":"old","usd":0.0005,"rounds":[],"tools":[],"tool_usd":[]}]}
        """#
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let r = try d.decode(CostReport.self, from: Data(json.utf8))
        XCTAssertEqual(r.byPurpose.first?.label, "Chat replies")
        XCTAssertEqual(r.messages[0].rounds.first?.cached, 4000)
        XCTAssertEqual(r.messages[0].toolUsd.first?.tool, "do")
        XCTAssertTrue(r.messages[1].rounds.isEmpty)
        XCTAssertEqual(0.0044.usdText, "$0.0044")
        XCTAssertEqual(0.5.usdText, "$0.500")
        XCTAssertEqual(2.0.usdText, "$2.00")
    }
}
