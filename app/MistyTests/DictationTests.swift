import XCTest
@testable import MistyCore

final class DictationTests: XCTestCase {
    func testRemovesFillersAndCapitalises() {
        XCTAssertEqual(DictationText.clean("um so uh I think we should go"), "So I think we should go")
        XCTAssertEqual(DictationText.clean("Well, umm, that works"), "Well, that works")
    }

    func testRemovesStutteredLittleWords() {
        XCTAssertEqual(DictationText.clean("I I think the the plan is fine"), "I think the plan is fine")
    }

    func testNeverRewordsRealRepeats() {
        // "had had" and "that that" are real English; only a list of little function words is collapsed
        XCTAssertEqual(DictationText.clean("He said that that he had had enough"), "He said that that he had had enough")
    }

    func testKeepsWordsThatContainFillerLetters() {
        XCTAssertEqual(DictationText.clean("The umbrella hummed, humming along"), "The umbrella hummed, humming along")
    }

    func testFixesWordsTheEarsAlwaysMishear() {
        XCTAssertEqual(DictationText.clean("apply it over here of socks", extra: []), "Apply it over here of sorts")
        XCTAssertEqual(DictationText.clean("It was a groq kind of socks", extra: []), "It was a Groq kind of sorts")
        XCTAssertEqual(DictationText.clean("compared to Grok in the cloud", extra: []), "Compared to Groq in the cloud")
        XCTAssertEqual(DictationText.clean("misty is open", extra: []), "Misty is open")
    }

    func testRealSocksStaySocks() {
        XCTAssertEqual(DictationText.clean("I lost a pair of socks", extra: []), "I lost a pair of socks")
        XCTAssertEqual(DictationText.clean("a whole drawer of socks", extra: []), "A whole drawer of socks")
    }

    func testReadsHisOwnWordList() throws {
        let path = NSTemporaryDirectory() + "dict-\(UUID().uuidString).txt"
        try "# his list\ngranular => Granola\nigcse => IGCSE\nbroken line\n".write(toFile: path, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(atPath: path) }
        let words = DictationText.userWords(path: path)
        XCTAssertEqual(words.count, 2)
        XCTAssertEqual(DictationText.clean("reverse engineer granular for my igcse", extra: words), "Reverse engineer Granola for my IGCSE")
        XCTAssertTrue(DictationText.userWords(path: path + ".missing").isEmpty)
    }

    func testTidiesSpacesAndLeavesPunctuationAlone() {
        XCTAssertEqual(DictationText.clean("  hello ,  world !  "), "Hello, world!")
        XCTAssertEqual(DictationText.clean(""), "")
    }
}
