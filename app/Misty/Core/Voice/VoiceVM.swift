import Foundation
import Observation
#if canImport(AppKit)
import AppKit
import ApplicationServices
#endif

/// What Isaac's words mean to the voice loop itself, before the chat brain sees them.
/// F5: how the always-on mic treats the room. `name`: only her name or the follow-up window reaches her, nothing else is sent
/// anywhere. `judge` (F7) also asks one small model about the rest (`addressed.judge`) and answers only `for_her`. The engine refuses to
/// hand the app this value until the judge has passed its test on his own labelled phrases, and refuses to ask a model before that too.
public enum AmbientMode: String, Sendable {
    case off, name, judge
    /// What he may pick by voice or in settings. `judge` is allowed here because the engine owns the gate (the pass file).
    public init?(word: String) {
        switch word.trimmingCharacters(in: .whitespaces).lowercased() {
        case "off": self = .off
        case "name": self = .name
        case "judge": self = .judge
        default: return nil
        }
    }
}

/// F6: one heard final as the engine's collector wants it. Words only; the engine scrubs them and keeps them only if collecting is on.
struct PhraseNote: Encodable, Sendable {
    var text: String, state: String, named: Bool, followup: Bool, sinceReply: Double?, outcome: String
    enum CodingKeys: String, CodingKey { case text, state, named, followup, sinceReply = "since_reply", outcome }
}

public enum RouteChoice: Equatable, Sendable { case silent, merge, heldRest, deliver, drop, judge, addressed }

public enum VoiceIntent: Equatable, Sendable {
    case hush          // "stop", "shut up", "wait": go quiet and keep listening
    case bye           // "bye", "that's all": end the session
    case yes, no       // answers to a spoken confirm
    case say(String)   // everything else goes to the chat

    public static func of(_ raw: String, confirming: Bool) -> VoiceIntent {
        let t = raw.lowercased().filter { $0.isLetter || $0 == " " || $0 == "'" }
            .split(separator: " ").joined(separator: " ")
        if t.isEmpty { return .hush }
        let hush = ["stop", "shut up", "wait", "hold on", "be quiet", "quiet", "pause", "stop talking", "ok stop",
                    "okay stop", "misty stop", "stop misty", "shush", "enough"]
        if hush.contains(t) { return .hush }
        let bye = ["bye", "bye misty", "goodbye", "that's all", "thats all", "that's it", "thats it", "thanks misty",
                   "thank you misty", "thanks that's all", "thanks thats all", "see you", "later", "i'm done", "im done"]
        if bye.contains(t) || t.hasPrefix("bye ") || t.hasSuffix(" bye") { return .bye }
        if confirming {
            let w = Set(t.split(separator: " ").map(String.init))
            let yes = !w.isDisjoint(with: ["yes", "yeah", "yep", "sure", "ok", "okay", "go", "do"]) && w.count <= 4
            let no = !w.isDisjoint(with: ["no", "nope", "don't", "dont", "cancel", "nah"]) && w.count <= 4
            if yes && no { return .say(raw.trimmingCharacters(in: .whitespacesAndNewlines)) }   // "yes no": not a clear yes (and the caller treats it as a no)
            if yes { return .yes }
            if no { return .no }
        }
        return .say(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    static let fillers: Set<String> = ["uh", "um", "umm", "uhm", "hmm", "hm", "mm", "mhm", "ah", "oh", "eh", "er", "huh"]

    static let hushWords: Set<String> = ["stop", "wait", "quiet", "shush", "pause", "enough", "hold"]

    static func words(_ s: String) -> [String] {
        s.lowercased().split(whereSeparator: { !$0.isLetter && $0 != "'" }).map(String.init).filter { $0.count >= 2 }
    }

    static let acks: Set<String> = ["yeah", "yes", "yep", "okay", "ok", "sure", "right", "no", "nah", "alright", "cool", "nice"]

    /// One acknowledgement word on its own ("Yeah."): the model's usual guess for room noise. Only a yes/no answer
    /// means anything that short, and those are caught before this (when she is confirming).
    public static func isLoneAck(_ s: String) -> Bool {
        let w = words(s)
        return w.count == 1 && acks.contains(w[0])
    }

    /// Her own voice heard back: most of the words are ones she just said.
    public static func isEcho(_ heard: String, of said: [String]) -> Bool {
        let w = words(heard).filter { !fillers.contains($0) }
        guard !w.isEmpty else { return true }
        let hers = Set(said.flatMap(words))
        return Double(w.filter(hers.contains).count) / Double(w.count) >= 0.6
    }

    /// Enough to cut her off while she talks: a stop word or her name. On MacBook speakers her echo comes back as loud
    /// as Isaac and the model turns it into words ("But so there"), so anything else said over her is ignored;
    /// a tap on Control+Option also cuts her off.
    public static func isBargeIn(_ partial: String, said: [String]) -> Bool {
        let w = words(partial)
        let t = w.joined(separator: " ")
        let stop = w.contains(where: { hushWords.contains($0) || names.contains($0) })
            || ["shut up", "be quiet", "hold on", "stop talking"].contains(where: t.contains)
        return stop && !isEcho(partial, of: said)
    }

    /// Isaac talking over her with real words ("No, I mean the other one"): two or more words that aren't fillers and
    /// aren't mostly her own sentence coming back. Echo cancellation keeps her voice out of the ears well enough that
    /// this is him, so she stops and takes it as his next message.
    public static func isTalkOver(_ heard: String, said: [String]) -> Bool {
        let real = words(heard).filter { !fillers.contains($0) }
        return real.count >= 2 && !isEcho(heard, of: said)
    }

    /// Online class with speakers: the mic hears the teacher come out of the Mac. A mic phrase of three or more words
    /// that is nearly all words the teacher just said is that echo, not Isaac. Stricter than `isEcho`, so a real
    /// question that reuses the teacher's terms ("what is a mole") still counts as him.
    public static func isTeacherEcho(_ heard: String, of teacher: [String]) -> Bool {
        let w = words(heard).filter { !fillers.contains($0) }
        guard w.count >= 3 else { return false }
        let theirs = Set(teacher)
        return Double(w.filter(theirs.contains).count) / Double(w.count) >= 0.8
    }

    /// He said her name (the ears sometimes hear "Missy").
    public static func namesHer(_ s: String) -> Bool {
        words(s).contains(where: names.contains)
    }

    // ---- F5: ambient mode ---------------------------------------------------------------------------------------------

    /// The names that wake her in ambient mode. `kit`, `kitt` and `missy` stay out: "pass me the first aid kit" and
    /// "Missy, dinner" are normal room talk. A renamed assistant adds her current name through `extra`.
    static let ambientNames: Set<String> = ["edith", "misty"]
    /// A name followed by one of these is not a call ("Edith Piaf", "Misty weather", "Edith is coming").
    static let ambientLeadBlock: Set<String> = ["piaf", "weather", "sky", "day", "morning", "mornings", "mountains", "hills", "eyes", "rain",
                                                "valley", "is", "was", "has", "had", "says", "said", "will"]
    /// A name after one of these is about her, not to her ("the hills look misty", "ask Edith", "where is Edith").
    static let ambientTailBlock: Set<String> = ["a", "the", "is", "was", "so", "very", "look", "looks", "feel", "feels", "quite", "too", "its",
                                                "it's", "gets", "getting", "be", "been", "are", "aunt", "with", "about", "to", "tell", "ask",
                                                "told", "asked", "see", "saw", "of", "for", "and", "my", "our", "call", "calling", "from", "meet"]
    static let ambientGreeting: Set<String> = ["hey", "hi", "okay", "ok"]

    /// Did he say her name to her? Only as the first word, the last word, or right after "hey". Strict on purpose: the mic is
    /// always on, so a name in the middle of a sentence is about her, not to her.
    public static func ambientName(_ s: String, extra: [String] = []) -> Bool {
        let names = ambientNames.union(extra.map { $0.lowercased() }.filter { !$0.isEmpty })
        let t = tokens(s)
        guard let first = t.first, let last = t.last else { return false }
        func calls(at i: Int) -> Bool {
            guard names.contains(t[i]) else { return false }
            return !(i + 1 < t.count && ambientLeadBlock.contains(t[i + 1]))
        }
        if t.count == 1 { return names.contains(first) }
        if names.contains(first), calls(at: 0) { return true }
        if t.count >= 2, ambientGreeting.contains(first), calls(at: 1) { return true }
        if names.contains(last), !ambientTailBlock.contains(t[t.count - 2]) { return true }
        return false
    }

    /// "Ambient on" / "stop listening for my name": true to turn it on, false to turn it off, nil when it is about neither.
    public static func ambient(_ s: String) -> Bool? {
        let w = words(s)
        guard w.contains("ambient") || (w.contains("listening") && w.contains("name")) else { return nil }
        return !w.contains(where: { ["off", "stop", "end", "exit", "disable", "quit", "don't", "dont", "cancel"].contains($0) })
    }

    /// The route for a phrase heard while the mic is open. Pure, so every branch is tested without a mic.
    public static func routeChoice(_ s: String, classMode: Bool, canMerge: Bool, hasHeld: Bool, followUp: Bool, ambient: AmbientMode,
                                   extraNames: [String] = []) -> RouteChoice {
        if classMode { return .silent }
        if canMerge { return .merge }
        if hasHeld { return .heldRest }
        let named = ambient == .off ? namesHer(s) : ambientName(s, extra: extraNames)
        if named || followUp { return .deliver }
        switch ambient {
        case .name: return .drop
        case .judge: return .addressed            // F7: the new judge; only `for_her` answers
        case .off: return .judge                  // live mode as it was: the old Jev judge
        }
    }

    /// Her name as the ears write it: EDITH (since 2026-11-15), plus Kit and Misty, the names she had before ("Missy" was a mishearing).
    static let names: Set<String> = ["edith", "edith's", "edyth", "kit", "kitt", "kit's", "kits", "misty", "missy", "misty's"]

    static let dangling: Set<String> = ["and", "or", "but", "so", "because", "like", "the", "a", "an", "to", "of",
                                        "about", "with", "for", "my", "your", "is", "are", "if", "then", "that",
                                        "which", "in", "on", "at", "from", "can", "could", "please", "also"]

    /// Stopped mid-thought ("tell me a paragraph about the"): wait for the rest before answering.
    public static func looksCutOff(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespaces)
        if t.hasSuffix("?") || t.hasSuffix("!") { return false }
        let w = words(t).filter { !fillers.contains($0) }
        guard let last = w.last else { return false }
        return dangling.contains(last) || t.hasSuffix(",") || t.hasSuffix("...") || t.lowercased().hasSuffix("i mean")
    }

    // MARK: F2, the early end of turn

    /// Words a sentence never ends on, with or without a full stop.
    static let weakAlways: Set<String> = [
        "is", "are", "was", "were", "am", "be", "been", "do", "does", "did", "can", "could", "would", "will", "should",
        "shall", "have", "has", "had", "what", "which", "who", "whom", "whose", "how", "when", "where", "why", "maybe",
        "just", "really", "actually", "well", "now", "not", "some", "any", "every", "each", "all", "more", "very", "too",
        "than", "as", "by", "into", "onto", "over", "under", "between", "after", "before", "during", "while", "until",
        "since", "though", "although", "once", "let", "lets", "let's", "want", "need", "going", "gonna", "wanna", "try",
        "think", "know", "say", "ask"]
    /// With no full stop or question mark, a request can also be cut off right after these ("tell me", "show me").
    static let weakOpen: Set<String> = ["me", "us", "them", "him", "her", "tell", "show", "give", "get", "make", "this", "that"]
    /// Never a last word, whatever the punctuation.
    static let hardDangling: Set<String> = ["and", "or", "but", "so", "because", "the", "a", "an", "to", "of", "with", "for",
                                            "my", "your", "if", "then", "like", "about", "from", "in", "on", "at", "also", "i"]
    /// A request or a question begins like this. Without a full stop or question mark nothing else counts as finished.
    static let starters: Set<String> = [
        "open", "close", "play", "pause", "set", "add", "remove", "delete", "send", "call", "text", "message", "remind",
        "tell", "show", "find", "search", "look", "check", "turn", "switch", "start", "read", "write", "create", "make",
        "schedule", "cancel", "move", "take", "give", "get", "what", "what's", "whats", "who", "when", "where", "why", "how",
        "which", "is", "are", "do", "does", "did", "can", "could", "would", "will", "should", "have", "has", "please",
        "list", "summarise", "summarize", "explain", "help", "teach", "quiz", "draw", "clear", "hide", "mute", "unmute",
        "skip", "next", "go", "let's", "lets"]

    static func tokens(_ s: String) -> [String] {
        s.lowercased().split(whereSeparator: \.isWhitespace).map { w -> String in
            var u = Substring(w)
            while let f = u.first, !(f.isLetter || f.isNumber) { u = u.dropFirst() }
            while let l = u.last, !(l.isLetter || l.isNumber) { u = u.dropLast() }
            return String(u)
        }.filter { !$0.isEmpty }
    }

    /// F2: the words are plainly finished, so the turn can end after 350 ms of quiet instead of 650. When unsure it says
    /// false, which only costs the old wait. It is never true for a phrase `looksCutOff` flags.
    public static func looksFinished(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let end = t.last, !looksCutOff(t) else { return false }
        if ",;:-\u{2013}\u{2014}\u{2026}".contains(end) || t.hasSuffix("...") { return false }
        let toks = tokens(t)
        let real = toks.filter { !fillers.contains($0) }
        guard real.count >= 2, let last = real.last, let first = real.first else { return false }
        func isNumber(_ w: String) -> Bool { !w.isEmpty && w.allSatisfy(\.isNumber) }
        if toks.count >= 2, isNumber(toks[toks.count - 1]), isNumber(toks[toks.count - 2]) { return false }   // a number being read out
        if hardDangling.contains(last) { return false }
        if end == "?" || end == "!" { return true }
        if end == "." { return !weakAlways.contains(last) }
        // no punctuation: a request or a question, at least three words, and not stopped on a word that wants more
        if t.filter({ $0 == "," }).count >= 2 { return false }                    // a list may go on
        return real.count >= 3 && starters.contains(first) && !weakAlways.contains(last) && !weakOpen.contains(last)
    }

    /// The message that was just sent came from an early end of turn (so a merge into it is an early end that was wrong).
    public static func endedEarly(earlyAt: Date?, sentAt: Date) -> Bool {
        guard let e = earlyAt else { return false }
        let d = sentAt.timeIntervalSince(e)
        return d >= -0.5 && d < 3
    }

    /// "Misty, class mode" / "Misty, class mode off": nil when the phrase isn't about class mode.
    public static func classMode(_ s: String) -> Bool? {
        let w = words(s)
        guard namesHer(s), w.contains("class") else { return nil }
        return !w.contains(where: { ["off", "stop", "end", "exit", "over", "done", "finished"].contains($0) })
    }

    /// A partial that means Isaac is really talking over her: a word of two letters or more that isn't a filler.
    /// Leftover echo of her own voice tends to come out as "Uh" or "Hmm", so those never cut her off.
    public static func isSpeech(_ partial: String) -> Bool {
        partial.lowercased().split(whereSeparator: { !$0.isLetter }).contains { $0.count >= 2 && !fillers.contains(String($0)) }
    }
}

public enum VoiceState: String, Sendable { case idle, starting, listening, thinking, speaking }

/// A plain file log for voice (`~/Misty/logs/voice.log`): the app's NSLog doesn't reach `log show` reliably.
enum VoiceLog {
    /// MISTY_VOICE_DEBUG=1: also log modifier keys and the mic level every second.
    static let debug = ProcessInfo.processInfo.environment["MISTY_VOICE_DEBUG"] != nil
    static let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Misty/logs/voice.log")
    /// Tests: lines go here instead of the real log file.
    nonisolated(unsafe) static var sink: ((String) -> Void)?
    static func write(_ line: String) {
        if let sink { sink(line); return }
        let s = "\(ISO8601DateFormatter().string(from: Date())) \(line)\n"
        guard let d = s.data(using: .utf8) else { return }
        if let h = try? FileHandle(forWritingTo: url) { h.seekToEndOfFile(); h.write(d); try? h.close() }
        else { try? d.write(to: url) }
    }
}

/// Season 3 Day 1: where the time goes in one tap-to-talk turn. Marks are ms since the second tap (the end of his
/// speech); the line goes to voice.log when the first sound is scheduled, and `scripts/turn_timing_report.py` joins it
/// with the engine's own `ENGINE_TIMING` line. Timing only: nothing here changes what she does.
struct TurnTimer {
    private(set) var turn = ""
    private var start = ContinuousClock.now
    private(set) var epoch = Date().timeIntervalSince1970
    private var marks: [(String, Int)] = []
    var firstClauseChars = 0
    var speechSeconds = 0.0
    var outputLatencyMs = 0
    var active: Bool { !turn.isEmpty }

    init() {}
    /// A fresh turn whose clock starts now (the tap that ends his speech); `turn` is filled in at send.
    init(tap: Bool) { start = .now; epoch = Date().timeIntervalSince1970; turn = tap ? "pending" : "" }

    mutating func bind(_ id: String) { if turn == "pending" { turn = id } }
    var isPending: Bool { turn == "pending" }
    func hasMark(_ name: String) -> Bool { marks.contains { $0.0 == name } }
    mutating func mark(_ name: String) {
        guard active, !marks.contains(where: { $0.0 == name }) else { return }
        marks.append((name, Int((ContinuousClock.now - start) / .milliseconds(1))))
    }
    func line(sinceWake: Double?, sinceLaunch: Double) -> String {
        let w = sinceWake.map { String(format: "%.0f", $0) } ?? "none"
        let body = marks.map { "\($0.0)=\($0.1)" }.joined(separator: " ")
        return String(format: "TURN_TIMING turn=%@ t_tap=%.3f since_wake_s=%@ since_launch_s=%.0f speech_s=%.1f first_clause_chars=%d out_latency=%d ",
                      turn, epoch, w, sinceLaunch, speechSeconds, firstClauseChars, outputLatencyMs) + body
    }
}

#if canImport(AppKit)
/// Voice chat: tap Command+Option (or Control+Option), talk, tap again to send, and Misty answers out loud. A tap
/// while she is thinking or talking cuts her off and starts listening. (Always-listening live mode is parked: the
/// code below stays, the hotkey no longer reaches it, and Class Notes still uses its ears.)
@MainActor
@Observable
public final class VoiceVM {
    public private(set) var state: VoiceState = .idle {
        didSet {   // live mode listens on the plain mic; echo cancellation comes on only when she is about to talk
            if state == .thinking || state == .speaking, !classMode, audio.running { audio.setEchoCancel(true) }
        }
    }
    public private(set) var line = ""                 // what the pill shows
    /// V8: the real mic loudness for the pill's meter, newest first.
    public var levels: [Float] { meter.history }
    private var meter = LevelSmoother()
    @ObservationIgnored private var meterRaw = LevelSmoother()
    @ObservationIgnored private var meterAt = Date.distantPast
    @ObservationIgnored private let liveWords = LiveReader()
    /// V1: her whole reply so far this turn, every sentence she has said, so the pill can show all of it. Kept for a few
    /// seconds after she stops (`lingering`) so he can still read it.
    public private(set) var reply = ""
    public private(set) var lingering = false
    @ObservationIgnored private var lingerTask: Task<Void, Never>?
    @ObservationIgnored private var lingerThenHide = false
    /// V6: the mouse is over the pill (it opens to the whole reply and stays while he reads), and how far he scrolled it.
    public var pillHover = false { didSet { if !pillHover { pillScroll = 0 } } }
    public var pillScroll: CGFloat = 0
    /// Her name (Misty or Edith) was just heard in ambient mode: the waiting dot lights up so he can see she caught it.
    /// It dims by itself after 3 s, or at once when the phrase is sent to her.
    public private(set) var nameHeard = false
    @ObservationIgnored private var nameGlowTask: Task<Void, Never>?
    private func lightUp() {
        nameHeard = true
        nameGlowTask?.cancel()
        nameGlowTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard !Task.isCancelled else { return }
            self?.nameHeard = false
        }
    }
    private func dim() { nameGlowTask?.cancel(); nameHeard = false }
    /// V1: the pill was clicked: open the full turn in Chat (set by AppState, which owns the window).
    @ObservationIgnored public var onOpenChat: (() -> Void)?
    public private(set) var problem: String?
    /// Off = muted: she answers in text only. Kept across launches (the menu bar's "Mute Misty").
    public var speakReplies = UserDefaults.standard.object(forKey: "misty.speakReplies") as? Bool ?? true {
        didSet { UserDefaults.standard.set(speakReplies, forKey: "misty.speakReplies") }
    }

    public var stateWord: String {
        if let problem { return problem }
        switch state {
        case .idle: return lingering ? "EDITH" : ""
        case .starting: return "Waking up"
        case .listening: return classMode ? "Class" : (talking ? "Listening" : (ambient != .off ? "Listening for your name" : "Live"))
        case .thinking: return "Thinking"
        case .speaking: return "EDITH"
        }
    }

    private let api: EngineAPI
    private let chat: ChatVM
    /// V4: the experimental local LFM3 voice (Settings toggle). Off at every launch.
    public let lfm: LFMHost
    @ObservationIgnored private var lfmTask: Task<Void, Never>?
    @ObservationIgnored private var lfmPlayer: LFMPlayer?
    @ObservationIgnored private var talkViaLFM = false       // decided on the key press, so a toggle mid-turn changes nothing
    private let audio = AudioIO()
    @ObservationIgnored private lazy var speaker: Speaker = {
        let s = Speaker(engine: audio.engine)
        s.onMark = { [weak self] name, latency in          // timing only (Season 3 Day 1)
            guard let self else { return }
            if name == "first_chunk", let u = self.updateStart {       // a line she started herself (the brief): how long to first sound
                self.updateStart = nil
                let d = ContinuousClock.now - u
                VoiceLog.write("UPDATE first_audio_ms=\(Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000))")
            }
            if name == "first_chunk" { self.tt.outputLatencyMs = latency }
            self.tt.mark(name)
            if name == "first_chunk", self.tt.active, !self.tt.isPending {
                VoiceLog.write(self.tt.line(sinceWake: self.wokeAt.map { Date().timeIntervalSince($0) },
                                            sinceLaunch: Date().timeIntervalSince(self.launchedAt)))
            }
        }
        return s
    }()
    @ObservationIgnored private var updateStart: ContinuousClock.Instant?    // timing only: a line she began on her own
    private var updateLine: (toldID: String, text: String)?                  // F4: a line she began herself, the mic is open for it
    private var updateMicHeld = false                                        // F4: the mic she opened for such a line is still open
    @ObservationIgnored private lazy var pill = VoicePill(vm: self)
    @ObservationIgnored let ears = Ears()       // shared with dictation: one model in memory
    @ObservationIgnored private lazy var macEars = Ears(sharing: ears)    // an online class's second stream: the Mac's sound
    @ObservationIgnored private let sysAudio = SystemAudio()
    private var classOnline = false
    private var macFailure: String?
    private var recentMac: [(at: Date, words: [String])] = []
    private var turn: String?                        // the chat turn being answered out loud
    private var splitter = SentenceSplitter()
    private var fed = ""                              // every piece of this turn's reply given to the splitter
    private var replyDone = false                     // the engine's final answer is in: late stream pieces are stale
    private var confirmID: (turn: String, id: String)?
    /// D3: a yes only counts once she has finished reading the preview aloud. Before that, anything he says is not an answer to it.
    private var confirmArmed = false
    private var lastHeard = Date()
    private var idleTask: Task<Void, Never>?
    private var speechEnd: ContinuousClock.Instant?
    private var firstAudioLogged = false
    private var said: [String] = []                  // her last few sentences, to tell her echo from Isaac
    private var frameHold = FrameHold()               // what he had open when he started to speak (step C3)
    private var spokeAt = Date.distantPast           // when she last finished or was cut off
    @ObservationIgnored private let narrator = Narrator()   // reads a drawing's captions while the picture builds
    private var talking = false                      // a tap-to-talk exchange is under way (recording, thinking or speaking)
    private var recording = false                    // the mic is open for a tap-to-talk message
    @ObservationIgnored private let mic = DictationRecorder()    // plain mic, no echo cancellation: nothing plays while he talks

    /// Class mode: she stays silent and does nothing; what she hears goes to the class session as transcript.
    public var classMode = false {
        didSet {
            if classMode != oldValue { VoiceLog.write("class_mode \(classMode)"); ears.setClassMode(classMode) }
            if classMode { lfm.classBegan() }               // V4: a class never runs beside the local model; its memory goes back
            if classMode { ambient = .off; ambientWaiting = false }       // F5: a class is silent, ambient mode turns off
            if classMode, updateLine != nil { end() }      // F4: a class opening closes the mic she opened for her own line
        }
    }
    /// F5: the mic is open and she answers only to her name or inside the follow-up window. Off at every launch (it needs his yes
    /// to the live consent each time he turns it on); it turns itself off in a class, in a call, and when the Mac sleeps.
    public private(set) var ambient: AmbientMode = .off {
        didSet { if ambient != oldValue { ears.setLean(ambient != .off) } }      // live mode reads the room's partials lean
    }
    /// Ambient voice: the mode he chose, kept across launches. The app turns ambient off by itself in a class, a call or sleep, and
    /// `keepAmbient` turns it back on once that is over. Only he turns it off for good (settings, "ambient off", "stop listening").
    static let ambientWantedKey = "misty.ambientWanted"
    public static var ambientWanted: AmbientMode {
        get { AmbientMode(word: wantedStore.string(forKey: ambientWantedKey) ?? "") ?? .off }
        set { wantedStore.set(newValue.rawValue, forKey: ambientWantedKey) }
    }
    /// Tests run inside the app's own bundle id: they keep this choice in their own store, or a test run turns his live mode off.
    static let wantedStore: UserDefaults = NSClassFromString("XCTestCase") != nil
        ? (UserDefaults(suiteName: "com.isaacmiranda.Misty.tests") ?? .standard) : .standard
    /// The talk key ends ambient for a turn of his own; it comes back after this long with nothing going on.
    static let ambientPause: TimeInterval = 120
    @ObservationIgnored private var ambientPausedUntil = Date.distantPast
    @ObservationIgnored private var keeper: Task<Void, Never>?
    /// When the mic last heard a phrase that was not for her: the judge's "is the room talking?" clue.
    @ObservationIgnored private var roomHeard: [Date] = []
    /// The live log he can open from the menu bar (memory only).
    public let thoughts = ThoughtLog()
    static let roomWindow: TimeInterval = 30
    /// F6: the app in front, asked only when a phrase is about to be kept. A private app in front means nothing is kept.
    @ObservationIgnored var frontBundle: () -> String? = { NSWorkspace.shared.frontmostApplication?.bundleIdentifier }
    @ObservationIgnored private var collectingOn = false
    @ObservationIgnored private var collectingCheckedAt = Date.distantPast
    public private(set) var ambientDrops = 0                       // phrases dropped with no model call, since it was turned on
    @ObservationIgnored private var ambientWaiting = false         // asked for by a spoken turn that is still ending: starts when it is done
    @ObservationIgnored private var lastAmbientWarm = Date.distantPast
    @ObservationIgnored private var tickCount = 0
    /// Her name when it is not Edith or Misty (a rename), for the name rule.
    @ObservationIgnored public var extraNames: [String] = []
    /// A call app that has the mic right now (Zoom, Teams, Meet...), else nil.
    @ObservationIgnored public var inCall: @MainActor () -> String? = { MeetingApps.inCall(MicUsers.current()) }
    @ObservationIgnored private var sleepWatch: NSObjectProtocol?
    /// The class being transcribed; in class mode every phrase goes here instead of to chat.
    @ObservationIgnored public weak var classSink: ClassVM?
    private var startedForClass = false
    @ObservationIgnored private var fileFeed: Task<Void, Never>?
    @ObservationIgnored private var recorder: ClassRecorder?   // a class's two tracks on disk, for the after-class pass
    static let followUp: TimeInterval = 8          // talking within this long after her reply is talking to her
    static let mergeWindow: TimeInterval = 6       // more words this soon after a message, before she speaks: one message
    static let holdFor: TimeInterval = 1.6         // a sentence that stopped mid-thought waits this long for the rest
    static let consentKey = "misty.liveConsent.v1"
    private var sent: (text: String, turn: String, at: Date)?   // the message being answered, for merging
    private var held: String?                      // a message for her that stopped mid-sentence
    private var holdTask: Task<Void, Never>?
    private var waiting: [String] = []             // messages for her heard while she was busy: sent next
    static let echoTail: TimeInterval = 0.7        // the room still rings with her voice this long after playback ends
    private var lastPartial = Date.distantPast
    private var tt = TurnTimer()                       // timing only (Season 3 Day 1)
    private var talkStart: ContinuousClock.Instant?
    private let launchedAt = Date()
    private var wokeAt: Date?
    @ObservationIgnored private var wakeWatch: NSObjectProtocol?

    public init(api: EngineAPI, chat: ChatVM, lfm: LFMHost = LFMHost(launcher: RealLFMLauncher())) {
        self.api = api
        self.chat = chat
        self.lfm = lfm
        wakeWatch = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.wokeAt = Date(); VoiceLog.write("WAKE") }
        }
        sleepWatch = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.willSleep() }
        }
        ears.onFrame = { [weak self] f in Task { @MainActor in self?.heard(f) } }
        macEars.onFrame = { [weak self] f in Task { @MainActor in self?.heardMac(f) } }
    }

    /// At launch: Misty's voice and her ears loaded, so the first tap is instant. Echo cancellation waits for live mode:
    /// it turns every other app's volume down while it is on.
    public func warm() async {
        lfm.reapOrphans()                                // V4: a local model left running by a crashed launch
        // One after the other: loading both onto the Neural Engine at once took ~63 s each (they queue for its
        // compiler anyway, and contending made both late). Ears first, so she can hear as soon as possible.
        let t0 = ContinuousClock.now
        let ready = await ears.warm()
        let earsMs = Int((ContinuousClock.now - t0) / .milliseconds(1))
        await speaker.warm()
        voiceRevision += 1                                    // the Settings voice lists were read while this was loading
        VoiceLog.write("warm ears=\(ready) ears_ms=\(earsMs) voice_ms=\(Int((ContinuousClock.now - t0) / .milliseconds(1)) - earsMs) "
                       + "accessibility=\(AXIsProcessTrusted()) mic=\(AudioIO.micAllowed)")
        if keeper == nil { keeper = Task { [weak self] in await self?.keepAmbient() } }
    }
    /// Bumped when the voice list or pick changes, so a Settings page that read them early (while the voice was still
    /// loading) draws again. `speaker` is not observable on its own.
    public private(set) var voiceRevision = 0
    public var voices: [String] { _ = voiceRevision; return speaker.voices }
    public var voiceName: String { _ = voiceRevision; return speaker.voice }
    public func setVoice(_ name: String) async { await speaker.setVoice(name); voiceRevision += 1 }
    public var speechRate: Float { speaker.rate }
    public func setSpeechRate(_ r: Float) { speaker.setRate(r) }
    /// The test phrase is never allowed while a class is on (silent mode, or a class session open).
    public nonisolated static func maySpeakSample(classMode: Bool, classOpen: Bool) -> Bool { !classMode && !classOpen }
    /// The speaker ran dry in a tap-to-talk turn, but the engine hasn't finished the reply (a tool is still running
    /// after "One sec."): the turn is not over. Her own yes/no question is the exception: it waits for his answer.
    public nonisolated static func moreComing(talking: Bool, replyDone: Bool, asking: Bool) -> Bool {
        talking && !replyDone && !asking
    }
    public var inClass: Bool { classMode || classSink?.isOpen == true }

    /// What the engine's gate needs to know about the voice: is he in a conversation with her right now.
    public var presenceVoice: String {
        if speaker.isSpeaking { return "speaking" }
        switch state {
        case .idle: return "idle"
        case .starting, .listening: return "listening"
        case .thinking: return "thinking"
        case .speaking: return "speaking"
        }
    }

    /// She speaks up on her own (the gate chose a good moment). Said out loud only when no conversation is going, she is
    /// not muted and no class is open; otherwise it is already in the chat as text.
    public func speakUpdate(_ text: String, toldID: String = "") {
        guard speakReplies, !inClass, state == .idle, !speaker.isSpeaking, updateLine == nil else { return }
        updateStart = .now
        if openUpdateMic() {                              // F4: the mic is open for the length of the line, so he can talk over it
            updateLine = (toldID, text)
            updateMicHeld = true
            said = [text]
            line = text
            state = .speaking
            ears.setSpeaking(true)                        // her echo needs a clearly louder voice to count
            speaker.onFinished = { [weak self] in self?.spokeAll() }
            VoiceLog.write("UPDATE_LINE mic open")
        }
        speaker.speak(text)                               // no mic (denied, ears not loaded): she speaks as she always did
    }

    /// F4: the mic for a line she began herself. Voice processing on (it cancels her own echo), the ears fed from it.
    /// False = no mic (not allowed, ears not ready, or it would not open): the line is then said as before.
    private func openUpdateMic() -> Bool {
        if let t = updateMic { return t.open() }
        guard AudioIO.micAllowed, Ears.installed, ears.ready, !audio.running else { return false }
        audio.voiceProcessing = true
        audio.restartOnRouteChange = false
        ears.reset()
        let e = ears
        do { try audio.start { samples in e.feed(samples) } } catch {
            VoiceLog.write("UPDATE_LINE mic start failed \(error)")
            return false
        }
        return true
    }

    private func closeUpdateMic() {
        updateMicHeld = false
        if let t = updateMic { t.close() } else { audio.stop() }       // the input is released, not left open
    }

    /// F4: her line ended and he did not talk over it: stop the ears and let go of the input. Nothing else changes.
    private func finishUpdateLine() {
        updateLine = nil
        closeUpdateMic()
        ears.reset()
        quiet()
        said = []
        state = .idle
        line = ""
        VoiceLog.write("UPDATE_LINE end talked_over=false")
    }

    /// F4: he talked over the line. She is already silent (bargeIn). What he says is a normal turn (the follow-up window is
    /// open), the Told you row is marked interrupted, and the session closes by itself when it goes quiet.
    private func talkedOverUpdate(_ u: (toldID: String, text: String), _ t0: ContinuousClock.Instant) {
        updateLine = nil
        let ms = Int((ContinuousClock.now - t0) / .milliseconds(1))
        VoiceLog.write("VOICE_TURN barge_stop_ms=\(ms) source=update out_ms=\(speaker.outputLatencyMs)")
        if !u.toldID.isEmpty {
            struct P: Encodable, Sendable { var id: String }
            let api = self.api, id = u.toldID
            Task { let _: JSONValue? = try? await api.call("told.interrupted", P(id: id)) }
        }
        lastHeard = Date()
        idleTask?.cancel()
        idleTask = Task { [weak self] in await self?.watchUpdateSession() }
    }

    /// F4: a conversation that began with talking over her own line ends on its own once nobody has said anything for a while.
    private func watchUpdateSession() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(1))
            guard state == .listening, turn == nil, waiting.isEmpty, held == nil, !ears.inSpeech else { continue }
            if Date().timeIntervalSince(max(lastHeard, spokeAt)) > Self.followUp + 4 {
                VoiceLog.write("UPDATE_LINE session quiet: closing")
                end()
                return
            }
        }
    }

    /// Test hooks (F4): the real mic is replaced, and "her line finished playing" is sent by hand.
    @ObservationIgnored var updateMic: (open: () -> Bool, close: () -> Void)?
    /// The line ended: the real speaker is silent too (in the app test host it has a voice and is really playing, so without this
    /// `presenceVoice` stays "speaking" and the next line is refused). Not a behaviour change: a line that ended is not playing.
    /// V1 test hook: put the pill into a state with a reply, as if she were saying it.
    func testShow(state: VoiceState, line: String = "", reply: String = "", lingering: Bool = false) {
        self.state = state; self.line = line; self.reply = reply; self.lingering = lingering
    }
    func testLineFinished() { speaker.stopNow(); speaker.onFinished?() }
    var updateLineOpen: Bool { updateLine != nil }

    /// Captions are read aloud only in an answer to a tap-to-talk message, and never in a class.
    public var canNarrate: Bool { talking && turn != nil && speakReplies && !inClass && state != .idle }

    /// Says each step's caption in turn, showing the step first. The chat reply is held until the last caption is done.
    /// `timing` (the Slate) also hears when each caption's audio starts and ends, from the speaker's real sample counts.
    public func narrate(_ captions: [String], show: @escaping @MainActor (Int) -> Void, timing: SlateTiming? = nil) {
        guard canNarrate else { return }
        if let t = timing {
            speaker.onSentenceStart = { [weak self] _, startsIn in
                guard let self, self.narrator.active else { return }
                t.start(self.narrator.step, startsIn)
            }
            speaker.onSentenceEnd = { [weak self] _, endsIn, secs in
                guard let self, self.narrator.active else { return }
                t.end(self.narrator.step, endsIn, secs)
            }
        }
        let silentStep: (@MainActor (Int) -> Void)? = timing.map { t in { @MainActor i in t.silent(i) } }
        let stoppedStep: (@MainActor () -> Void)? = timing.map { t in { @MainActor in t.stopped() } }
        narrator.run(captions, show: show, speak: { [weak self] text in
            guard let self else { return false }
            self.say(text, caption: true)
            return self.speaker.isSpeaking
        }, silent: silentStep, stopped: stoppedStep, done: { [weak self] rest in
            guard let self else { return }
            for s in rest { self.say(s) }
            if rest.isEmpty, self.replyDone, !self.speaker.isSpeaking { self.spokeAll() }
        })
    }
    /// Settings: says a fixed line in the current voice at the current speed. Only the "Play test phrase" button calls this.
    public func speakSample() {
        guard Self.maySpeakSample(classMode: classMode, classOpen: classSink?.isOpen == true) else { return }
        speaker.speakSample()
    }

    /// The hotkey. Idle: start listening. Listening: send what was said. While she thinks or speaks: cut her off and
    /// listen again. Class mode (or old live mode) already on: the tap ends it, as it always did.
    public func tap() async {
        if state == .idle, speaker.isSpeaking {          // a line she started on her own (the brief): the key cuts her off first
            let t0 = ContinuousClock.now
            speaker.stopNow()
            updateStart = nil
            let d = ContinuousClock.now - t0
            VoiceLog.write("UPDATE_INTERRUPT stop_ms=\(Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000)) "
                           + Self.keyToStop(speaker.outputLatencyMs))
        }
        if ambientWaiting { ambient = .off; ambientWaiting = false }      // F5: a tap before it started cancels the start
        if ambient != .off { ambientPausedUntil = Date().addingTimeInterval(Self.ambientPause); VoiceLog.write("AMBIENT paused by the key") }
        if state != .idle && !talking || state == .starting { end(); return }
        if recording { await finishTalk(); return }
        if talking && (state == .thinking || state == .speaking) { interrupt() }
        await startTalk()
    }

    // MARK: the wake word

    /// Isaac said her name. Starts exactly the session the hotkey starts from idle (`startTalk`), with a soft chime first.
    /// `base` is what only the app knows (class open, a call, dictation, paused); the voice's own state is read here. Refused
    /// when the gate is closed, which is also how a detection that lands while she is speaking or a turn is running is dropped.
    @discardableResult
    public func wake(base: WakeGate = WakeGate()) async -> Bool {
        let g = wakeGate(base)
        guard g.open else { VoiceLog.write("WAKE refused (\(g.blocker ?? "?"))"); return false }
        wakeChime()
        await startTalk(viaWake: true)
        return true
    }

    /// The gate with the voice's own state filled in: no session, no spoken line (or its echo), no turn, no class, no ambient mode.
    public func wakeGate(_ base: WakeGate) -> WakeGate {
        var g = base
        g.voiceIdle = state == .idle
        g.speaking = speaker.isSpeaking || updateLine != nil || Date().timeIntervalSince(spokeAt) < Self.echoTail + 0.8
        g.turnRunning = talking || recording || turn != nil
        g.inClass = base.inClass || inClass
        g.ambient = ambient != .off || ambientWaiting
        return g
    }

    /// The words he said after "Edith", taken from the wake listener as the recorder opens. Set by `WakeWiring`.
    @ObservationIgnored public var wakeTail: @MainActor () -> [Float] = { [] }
    @ObservationIgnored private var wakeWatcher: WakeTurnWatcher?
    @ObservationIgnored var wakeChime: () -> Void = { Sound.shared.play(.wake) }
    /// Tests: stands in for the whole of `startTalk`, so the hotkey and the wake word can be seen to reach the same place.
    @ObservationIgnored var startTalkOverride: ((Bool) async -> Void)?

    /// A wake-started turn: he went quiet (send what he said) or never said anything (close without a word).
    private func wakeTurnDecided(_ d: WakeTurn.Decision) async {
        guard wakeWatcher != nil, recording, talking, state == .listening else { return }
        switch d {
        case .keep: break
        case .send: VoiceLog.write("WAKE turn: he went quiet, sending"); await finishTalk()
        case .giveUp: VoiceLog.write("WAKE turn: nothing said, closing"); end()
        }
    }

    /// F1: ask the engine to warm the prompt cache (and wake its embedding model) while he is still starting to talk, so the
    /// reply's first word does not pay for a cold cache. Fire and forget: nothing waits for it and a failure is silent.
    /// Never in class mode (the engine also refuses there).
    public func warmCache() {
        guard !classMode else { return }
        struct P: Encodable, Sendable { var voice = true }
        let api = self.api
        Task.detached { let _: JSONValue? = try? await api.call("chat.warm", P()) }
    }

    private func startTalk(viaWake: Bool = false) async {
        if let o = startTalkOverride { await o(viaWake); return }         // tests: the hotkey and the wake word meet here
        talkViaLFM = lfm.canRoute(classMode: classMode)  // V4: decided here, once per press
        if !talkViaLFM { warmCache() }                   // first thing on the key, before the mic permission or the ears
        talkStart = .now
        talking = true
        problem = nil
        line = ""
        cancelLinger(); reply = ""
        state = .starting
        pill.show()
        guard await AudioIO.askMic() else { return fail("Mic is off: Settings > Permissions") }
        if !talkViaLFM {                                 // the local model hears the sound itself: no ears needed
            guard Ears.installed else { return fail("Voice model missing") }
            if !ears.ready {
                line = "Loading her ears, about a minute"
                while !ears.ready {
                    try? await Task.sleep(for: .milliseconds(250))
                    if state != .starting { return }            // tapped again while waiting
                    if ears.loadFailed { return fail("Ears didn't load. Free some disk space, then restart EDITH") }
                }
                line = ""
            }
        }
        meterRaw.reset(); meter = meterRaw
        mic.onLevel = { [weak self] r in Task { @MainActor in self?.pushLevel(r) } }
        // The wake word: the words said after "Edith" in the same breath come across from the listener (it hands over just as
        // this mic opens), and the turn ends by itself when he goes quiet, since his hands are not on the keyboard.
        let carried = viaWake ? wakeTail() : []
        let watcher = viaWake ? WakeTurnWatcher { [weak self] d in Task { @MainActor in await self?.wakeTurnDecided(d) } } : nil
        wakeWatcher = watcher
        if let watcher {
            watcher.feed(carried)
            mic.onChunk = { chunk in watcher.feed(chunk) }
        }
        do { try mic.start(prefill: carried) } catch {
            VoiceLog.write("talk: mic start failed \(error)")
            mic.onChunk = nil; wakeWatcher = nil
            return fail("Couldn't open the mic")
        }
        if !talkViaLFM {                                 // V8: his words rise into the pill as he says them
            let m = mic
            liveWords.start(ears: ears, snapshot: { m.snapshot() }) { [weak self] t in
                guard let self, self.recording, self.state == .listening else { return }
                self.line = t
            }
        }
        speaker.onFinished = { [weak self] in self?.spokeAll() }
        recording = true
        state = .listening
        grabFrame()                                      // beside the mic, never before it
        line = viaWake ? (confirmID == nil ? "Listening" : "Yes or no?") : (confirmID == nil ? "Tap again to send" : "Yes or no? Tap again to send")
        VoiceLog.write(viaWake ? "talk: listening (wake word, \(carried.count) samples carried)" : "talk: listening")
        if let t0 = talkStart {
            let since = wokeAt.map { String(format: "%.0f", Date().timeIntervalSince($0)) } ?? "none"
            VoiceLog.write("TALK_START listen_ms=\(Int((ContinuousClock.now - t0) / .milliseconds(1))) ears_ready=\(ears.ready) "
                           + "since_wake_s=\(since) since_launch_s=\(Int(Date().timeIntervalSince(launchedAt)))")
            talkStart = nil
        }
    }

    private func finishTalk() async {
        tt = TurnTimer(tap: true)
        tt.mark("tap_end")
        recording = false
        mic.onChunk = nil; wakeWatcher = nil
        liveWords.stop()
        let wave = mic.stop()
        tt.mark("mic_stopped")
        state = .thinking
        line = "Reading…"
        let secs = Double(wave.count) / 16000
        tt.speechSeconds = secs
        let peak = wave.reduce(Float(0)) { max($0, abs($1)) }
        guard secs >= 0.5, peak >= 0.01 else {               // a quick second tap is a cancel
            VoiceLog.write(String(format: "talk: cancelled (%.1fs, peak %.3f)", secs, peak))
            return end()
        }
        if talkViaLFM { talkViaLFM = false; lfmTurn(wave, secs: secs); return }      // V4
        let raw = await ears.readWhole(wave)
        tt.mark("transcript")
        guard talking, state == .thinking else { return }    // tapped away while it was being read
        let text = DictationText.clean(raw)
        DictationSamples.keepIfOn(wave, raw: raw, cleaned: text)
        VoiceLog.write(String(format: "talk: %.1fs heard %d chars", secs, text.count))
        guard text.contains(where: \.isLetter) else { return fail("Didn't catch that") }
        speechEnd = .now
        if confirmID != nil { act(on: text); return }       // D3: while a yes is wanted, whatever he says is the answer (only a clear yes is a yes)
        switch VoiceIntent.of(text, confirming: false) {
        case .hush: end()
        case .yes, .no: act(on: text)
        case .bye, .say: send(text)
        }
    }

    /// A tap while she thinks or talks: quiet now, drop the rest of the reply, forget what was waiting.
    private func interrupt() {
        let was = state.rawValue
        stopLFM()
        speaker.stopNow()
        VoiceLog.write("talk: interrupted \(was) " + Self.keyToStop(speaker.outputLatencyMs))
        quiet()
        if let t = turn { stopTurn(t) }
        narrator.cancel()
        turn = nil; confirmID = nil; confirmArmed = false; sent = nil; held = nil; waiting = []
    }

    /// Live mode (parked: the hotkey no longer comes here). It turned the mic on and off. While it is on the mic stays open and she answers what is meant for her.
    public func toggle() async {
        if state == .idle {
            guard Self.agreed() else { return }
            await begin()
        } else {
            end()
        }
    }

    /// Live mode hears everything in the room, so Isaac agrees to how that works once, before the first use.
    static func agreed() -> Bool {
        if UserDefaults.standard.bool(forKey: consentKey) { return true }
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = "Turn on live mode?"
        a.informativeText = """
        While live mode is on, Misty's mic stays open and she hears everything said around you, not only to her.

        • Speech is turned into text on this Mac. No audio is ever saved or sent.
        • Each sentence is checked by Jev to tell whether it was meant for Misty. Those, and her answers, go to the AI \
        models through OpenRouter with zero data retention.
        • From other people's talk she keeps only to-dos for you ("dinner at 8"), as facts you can see and delete.
        • Tell the people around you that Misty is listening. Class mode keeps her silent.
        • Control+Option turns live mode off at any time.
        """
        a.addButton(withTitle: "I agree")
        a.addButton(withTitle: "Not now")
        guard a.runModal() == .alertFirstButtonReturn else { return false }
        UserDefaults.standard.set(true, forKey: consentKey)
        return true
    }

    // MARK: session

    private func begin() async {
        state = .starting
        problem = nil
        line = ""
        cancelLinger(); reply = ""
        audio.voiceProcessing = false                    // plain mic; live mode turns echo cancellation on only while she talks
        audio.restartOnRouteChange = classMode           // a class mic that a route change stopped (AirPods, a call app) reopens itself
        pill.show()
        let t0 = ContinuousClock.now
        guard await AudioIO.askMic() else { return fail("Mic is off: Settings > Permissions") }
        guard Ears.installed else { return fail("Voice model missing") }
        // a class records from the first second: the mic opens now, and the ears only get it once they're loaded
        if classMode, ProcessInfo.processInfo.environment["MISTY_CLASS_WAV"] == nil, !audio.running {
            ears.reset()
            let e = ears, rec = recorder
            do {
                try audio.start { samples in rec?.mic.append(samples); if e.ready { e.feed(samples) } }
            } catch {
                VoiceLog.write("mic start failed \(error)")
                return fail("Couldn't open the mic")
            }
        }
        // macOS prepares her ears for the Neural Engine at every launch (~60 s, mostly waiting on its compiler):
        // turned on before that is done, live mode waits and starts by itself instead of failing.
        if !ears.ready {
            line = "Loading her ears, about a minute"
            while !ears.ready {
                try? await Task.sleep(for: .milliseconds(250))
                if state != .starting { return }            // turned off while waiting
                if ears.loadFailed { return fail("Ears didn't load. Free some disk space, then restart EDITH") }
            }
            line = ""
        }
        if classMode, let wav = ProcessInfo.processInfo.environment["MISTY_CLASS_WAV"] {
            // the silent class test: a sound file goes straight into her ears at real speed, the mic stays shut
            ears.reset()
            let e = ears, rec = recorder
            let samples = (try? AudioIO.fileSamples(wav)) ?? []
            VoiceLog.write("class wav \(samples.count) samples")
            fileFeed = Task.detached {
                var i = 0
                while i < samples.count, !Task.isCancelled {
                    let chunk = Array(samples[i..<min(i + 1600, samples.count)])
                    rec?.mic.append(chunk)
                    e.feed(chunk)
                    i += 1600
                    try? await Task.sleep(for: .milliseconds(100))
                }
                try? await Task.sleep(for: .seconds(3))
                await MainActor.run { NotificationCenter.default.post(name: .mistyClassWavDone, object: nil) }
            }
            state = .listening
            return
        }
        if !audio.running { do {
            ears.reset()
            let e = ears
            try audio.start { [weak self] samples in
                e.feed(samples)
                let r = LevelSmoother.rms(samples)
                Task { @MainActor in self?.pushLevel(r) }
            }
        } catch {
            VoiceLog.write("mic start failed \(error)")
            return fail("Couldn't open the mic")
        } }
        speaker.onFinished = { [weak self] in self?.spokeAll() }
        state = .listening
        log("begin_ms", t0)
        lastHeard = Date()
        idleTask = Task { [weak self] in await self?.watchIdle() }
    }

    public func end() {
        VoiceLog.write("end state=\(state.rawValue)")
        if !ambientWaiting, ambient != .off { VoiceLog.write("AMBIENT off"); ambient = .off }
        fileFeed?.cancel()
        idleTask?.cancel()
        narrator.cancel()
        if updateLine != nil {                           // F4: the key (or a class) cut her own line short
            let t0 = ContinuousClock.now
            speaker.stopNow()
            let d = ContinuousClock.now - t0
            VoiceLog.write("UPDATE_INTERRUPT stop_ms=\(Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000)) "
                           + Self.keyToStop(speaker.outputLatencyMs))
            updateLine = nil
        }
        if updateMicHeld { updateMicHeld = false; updateMic?.close() }          // F4: whatever ends the session lets the input go
        speaker.stopNow()
        stopLFM()
        talkViaLFM = false
        if let t = turn { stopTurn(t) }
        liveWords.stop()
        mic.onChunk = nil; wakeWatcher = nil
        if recording { _ = mic.stop() }
        recording = false
        talking = false
        audio.stop()
        quiet()
        ears.reset()
        if !classMode { stopMac() }
        turn = nil
        confirmID = nil
        confirmArmed = false
        sent = nil
        held = nil
        holdTask?.cancel()
        waiting = []
        state = .idle
        line = ""
        if lingerThenHide, !reply.isEmpty {              // V1: her reply stays up a few seconds, then the pill goes
            lingerThenHide = false
            startLinger(hideAfter: true)
        } else {
            cancelLinger(); reply = ""
            pill.hide()
        }
    }

    // MARK: V4, the experimental local voice

    private func stopLFM() {
        lfmTask?.cancel(); lfmTask = nil
        lfmPlayer?.stop(); lfmPlayer = nil
    }

    /// His recording goes to the local model; its voice comes back and plays. No engine, no memory, no tools: a feel test.
    private func lfmTurn(_ wave: [Float], secs: Double) {
        guard let port = lfm.port, lfm.state == .ready else { return fail("LFM3 voice isn't running") }
        line = "LFM3 is thinking…"
        let player = LFMPlayer()
        do { try player.start() } catch { return fail("Couldn't play sound") }
        lfmPlayer = player
        lfmTask = Task { [weak self] in
            do {
                var started = false
                let r = try await LFMClient.run(port: port, wave: wave) { data in
                    guard let self, !started else { player.enqueue(data); return }
                    started = true
                    self.state = .speaking
                    self.line = "LFM3"
                    player.enqueue(data)
                }
                VoiceLog.write(LFMWire.logLine(firstAudioMs: r.firstAudioMs, audioSeconds: r.audioSeconds, listenSeconds: secs))
                player.finish { Task { @MainActor in if self?.lfmPlayer === player { self?.end() } } }
            } catch is CancellationError {
            } catch {
                VoiceLog.write("LFM turn_failed \(error.localizedDescription)")
                self?.fail("LFM3 voice failed")
            }
        }
    }

    // MARK: V1, the reply stays readable

    private func startLinger(hideAfter: Bool) {
        lingerTask?.cancel()
        lingering = true
        lingerTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(PillLayout.lingerSeconds))
            // he is reading it. V11: only while the mouse is really on it, a missed mouse-exit used to keep it up forever.
            while let me = self, me.pillHover, !Task.isCancelled {
                if !me.pill.mouseInside { me.pillHover = false; break }
                try? await Task.sleep(for: .milliseconds(300))
            }
            guard let self, !Task.isCancelled else { return }
            self.lingering = false
            self.reply = ""
            if hideAfter, self.state == .idle { self.pill.hide() }
        }
    }

    private func cancelLinger() {
        lingerTask?.cancel()
        lingerTask = nil
        lingering = false
        lingerThenHide = false
    }

    /// V8: loudness to the meter. The pill redraws at most ~30 times a second for it.
    private func pushLevel(_ rms: Float) {
        meterRaw.push(rms)
        let now = Date()
        guard now.timeIntervalSince(meterAt) > 0.033 else { return }
        meterAt = now
        meter = meterRaw
    }

    /// What the pill shows right now (see `PillLayout.shown`).
    /// V6: which shape the glass card takes right now.
    var pillMode: PillLayout.Mode { PillLayout.mode(state: state, line: line, reply: reply, lingering: lingering, problem: problem) }

    var pillShown: (text: String, isReply: Bool) { PillLayout.shown(state: state, line: line, reply: reply, lingering: lingering) }

    /// The pill was clicked (not dragged): open this turn in Chat. The pill goes at once, he is reading it there.
    public func pillClicked() {
        let hadReply = !reply.isEmpty
        cancelLinger()
        if state == .idle { reply = ""; pill.hide() }
        if hadReply || state != .idle { onOpenChat?() }
    }

    private func fail(_ why: String) {
        VoiceLog.write("fail \(why)")
        problem = why
        state = .listening
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(2.5))
            if self.problem == why { self.end(); self.problem = nil }
        }
    }

    private func watchIdle() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(1))
            let peak = audio.takePeak()
            if VoiceLog.debug { VoiceLog.write("tick \(state.rawValue) mic_peak=\(peak) \(ears.vadState())") }
            tickCount += 1
            if tickCount % 3 == 0 { watchTick() }
            // live mode stays on until the hotkey: no timeout
        }
    }

    // MARK: hearing

    func testHeard(_ f: STTFrame) { heard(f) }
    func testStartAmbient(_ m: AmbientMode) { ambient = m; state = .listening }
    func testFront(_ bundle: String?) { frontBundle = { bundle } }
    func testWillSleep() { willSleep() }
    func testWatchTick() { watchTick() }

    // MARK: ambient mode (F5)

    /// Turns ambient mode on or off. `true` when it holds the mode he asked for. On needs the live consent (once), no class
    /// and no call; it starts when a spoken turn that is still ending is done, so her answer to "ambient on" is not cut off.
    @discardableResult
    public func setAmbient(_ mode: AmbientMode) async -> Bool {
        Self.ambientWanted = mode
        ambientPausedUntil = .distantPast
        return await startAmbient(mode)
    }

    private func startAmbient(_ mode: AmbientMode) async -> Bool {
        if mode == .off {
            ambientWaiting = false
            if ambient != .off { ambient = .off; if state != .idle { end() } }
            return true
        }
        guard !inClass, inCall() == nil, Self.agreed() else { return false }
        ambient = mode
        ambientDrops = 0
        ambientWaiting = true
        VoiceLog.write("AMBIENT on mode=\(mode.rawValue)")
        var waited = 0
        while ambientWaiting, talking || state != .idle, waited < 120 { try? await Task.sleep(for: .milliseconds(250)); waited += 1 }
        guard ambientWaiting, ambient == mode, state == .idle else { ambientWaiting = false; if ambient == mode, state != .idle { return true }; ambient = .off; return false }
        ambientWaiting = false
        await begin()
        return ambient == mode
    }

    func willSleep() {
        guard ambient != .off else { return }
        VoiceLog.write("AMBIENT off (the Mac is going to sleep)")
        ambientWaiting = false
        end()
    }

    /// Phrases heard in the last 30 s that were not for her (pruned as it counts).
    func roomCount(now: Date = Date()) -> Int {
        roomHeard.removeAll { now.timeIntervalSince($0) > Self.roomWindow }
        return roomHeard.count
    }
    func testRoomHeard(_ d: Date) { roomHeard.append(d) }

    /// Whether `keepAmbient` may turn his chosen mode back on right now: it is off, nothing else holds the mic or her voice, no class,
    /// no call, the live consent was given, the key's pause is over, and the Mac woke more than 5 s ago.
    func canResumeAmbient(now: Date = Date(), consent: Bool, call: String?) -> Bool {
        Self.ambientWanted != .off && ambient == .off && !ambientWaiting && state == .idle && !recording && !talking
            && !speaker.isSpeaking && updateLine == nil && !inClass && call == nil && consent && now >= ambientPausedUntil
            && now.timeIntervalSince(wokeAt ?? .distantPast) > 5
    }

    /// Runs for the life of the app: puts ambient back after a launch, a sleep, a class, a call or the key's pause.
    private func keepAmbient() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(5))
            guard canResumeAmbient(consent: UserDefaults.standard.bool(forKey: Self.consentKey), call: inCall()) else { continue }
            let want = Self.ambientWanted
            VoiceLog.write("AMBIENT resume mode=\(want.rawValue)")
            _ = await startAmbient(want)
        }
    }

    /// Every few seconds while the mic is open: a call that takes the mic switches ambient mode off.
    func watchTick() {
        guard ambient != .off, let app = inCall() else { return }
        VoiceLog.write("AMBIENT off (a call: \(app))")
        ambientWaiting = false
        end()
    }

    /// Class Notes turns the ears on (in class mode: silent) or off. Live mode already on stays on afterwards.
    /// `online`: the Mac's sound is listened to as well, as the teacher (any platform), and the mic is Isaac.
    public func listenForClass(_ on: Bool, online: Bool = false, session: String? = nil, pausing: Bool = false) async -> Bool {
        if !on {
            // a pause (lid closed) keeps the recorder and its open files: the resume carries on in them, it must not start over
            defer { if !pausing { recorder?.finish(); recorder = nil } }
            fileFeed?.cancel()
            stopMac()
            if let last = await ears.flush(), let c = classSink {     // the words he heard just before End
                VoiceLog.write("class heard \(last.count) chars (flushed)")
                await c.heard(last)
            }
            if classOnline, let last = await macEars.flush(), let c = classSink {
                VoiceLog.write("class heard \(last.count) chars from the Mac (flushed)")
                await c.heard(last, source: "mac")
            }
            classOnline = false
            classMode = false
            if startedForClass { startedForClass = false; end() }
            return true
        }
        guard state != .idle || Self.agreed() else { return false }
        // Live mode's mic runs with echo cancellation, which silences the Mac's-sound tap and strips a class played on
        // the speakers. A class needs a plain mic, so live mode already on is turned off and the class opens its own.
        if state != .idle, !classMode { end() }
        classMode = true
        classOnline = online
        // online classes are recorded as two tracks; a resume after a pause carries on in the same recorder
        let wavTest = ProcessInfo.processInfo.environment["MISTY_CLASS_WAV"] != nil
        if online || wavTest, let sid = session {
            if recorder?.session != sid { recorder?.finish(); recorder = ClassRecorder(session: sid) }
        } else {
            recorder = nil
        }
        if online { startMac() }                            // the Mac's sound is recorded from the first second too
        if state == .idle {
            startedForClass = true
            await begin()
        }
        let ok = state != .idle && problem == nil
        if !ok { stopMac(); recorder?.finish(); recorder = nil }
        return ok
    }

    private func startMac() {
        macFailure = nil
        recentMac = []
        macEars.reset()
        macEars.setClassMode(true)
        let m = macEars, e = ears, rec = recorder
        do {
            try sysAudio.start { s in rec?.mac.append(s); if e.ready { m.feed(s) } }
            VoiceLog.write("class online: system audio tap on")
        } catch {
            macFailure = "Couldn't listen to the Mac's sound (\(error))"
            VoiceLog.write("class online: tap failed \(error)")
        }
    }

    private func stopMac() {
        if sysAudio.running { sysAudio.stop(); VoiceLog.write("class online: system audio tap off") }
    }

    /// Why an online class isn't hearing the Mac, or nil when it is (or hasn't had time to).
    public func macAudioNote() -> String? {
        if let f = macFailure { return f }
        if let s = sysAudio.silentFor, s > 20 {
            return "Not hearing the Mac's sound yet. Allow EDITH under System Audio Recording in Privacy & Security."
        }
        if sysAudio.stalledFor != nil { return "Nothing has come from the Mac's sound for a while." }
        return nil
    }

    /// What the Mac's sound said (the teacher). Never reaches chat: a class is transcript only.
    private func heardMac(_ f: STTFrame) {
        guard classMode, classOnline, let c = classSink, c.isOpen else { return }
        switch f {
        case .partial(let p):
            noteMac(p)
            c.partial(p, source: "mac")
        case .final(let t):
            noteMac(t)
            VoiceLog.write("class heard \(t.count) chars from the Mac")
            Task { await c.heard(t, source: "mac") }
        case .error(let why):
            VoiceLog.write("mac stt error \(why)")
        }
    }

    private func noteMac(_ text: String) {
        let now = Date()
        recentMac.removeAll { now.timeIntervalSince($0.at) > 12 }
        recentMac.append((now, VoiceIntent.words(text)))
    }

    private func micIsTeacherEcho(_ text: String) -> Bool {
        guard classOnline else { return false }
        let now = Date()
        recentMac.removeAll { now.timeIntervalSince($0.at) > 12 }
        return VoiceIntent.isTeacherEcho(text, of: recentMac.flatMap(\.words))
    }

    private func heard(_ f: STTFrame) {
        if classMode, let c = classSink, c.isOpen {          // a class: transcript only, nothing reaches chat or Jev
            switch f {
            case .partial(let p): if !micIsTeacherEcho(p) { c.partial(p) }
            case .final(let t):
                if micIsTeacherEcho(t) { VoiceLog.write("class mic: the Mac's sound heard back, dropped"); return }
                VoiceLog.write("class heard \(t.count) chars")
                if VoiceIntent.classMode(t) == false { Task { await c.end() }; return }
                Task { await c.heard(t) }
            case .error(let why):
                VoiceLog.write("stt error \(why)")
                Task { await c.pause(reason: "mic") }
            }
            return
        }
        guard state != .idle else { return }
        if ambient == .off { VoiceLog.write("heard \(f) state=\(state.rawValue)") }
        else { VoiceLog.write("heard \(Self.ambientKind(f)) state=\(state.rawValue)") }      // F5: the room's words are never written down
        switch f {
        case .partial(let p):
            lastPartial = Date()
            if ambient != .off, state == .listening, VoiceIntent.ambientName(p, extra: extraNames) {
                lightUp()                                    // her name was heard: the dot lights up at once
                if Date().timeIntervalSince(lastAmbientWarm) > 20 {
                    lastAmbientWarm = Date()
                    warmCache()                              // her name is on its way: the cache warms while he finishes the sentence
                }
            }
            if state == .speaking && (VoiceIntent.isBargeIn(p, said: said) || VoiceIntent.isTalkOver(p, said: said)) {
                bargeIn()
            }
            else if state == .thinking && VoiceIntent.isBargeIn(p, said: said) { bargeIn() }
            if state == .listening { line = ambient == .off ? p : ""; grabFrame() }
        case .final(let text):
            // while she talks, or just after, a phrase that is mostly her own words is her echo
            let recent = state == .speaking || Date().timeIntervalSince(spokeAt) < 2
            if recent && VoiceIntent.isEcho(text, of: said) { VoiceLog.write("echo ignored"); return }
            if state == .speaking {
                guard VoiceIntent.isBargeIn(text, said: said) || VoiceIntent.isTalkOver(text, said: said) else { return }
                bargeIn()
            }
            lastHeard = Date()
            speechEnd = .now
            if ambient != .off, state == .listening, VoiceIntent.ambientName(text, extra: extraNames) { lightUp() }   // a name no partial caught
            act(on: text)
        case .error(let why):
            VoiceLog.write("stt error \(why)")
            fail("Lost the mic stream")
        }
    }

    /// F5: what a frame was, with none of its words, for the log.
    static func ambientKind(_ f: STTFrame) -> String {
        switch f {
        case .partial(let p): return "partial chars=\(p.count)"
        case .final(let t): return "final chars=\(t.count)"
        case .error(let why): return "error \(why)"
        }
    }

    /// Isaac talked over her: silence now, drop the rest of the reply, listen.
    private func bargeIn() {
        let t0 = ContinuousClock.now
        speaker.stopNow()
        narrator.cancel()                               // a Slate mid-line finishes that line at once
        quiet()
        if confirmID == nil {                           // a turn waiting on "yes or no?" stays alive for the answer
            if let t = turn { stopTurn(t) }
            turn = nil
        }
        state = .listening
        if let u = updateLine { talkedOverUpdate(u, t0) } else { log("barge_stop_ms", t0) }
    }

    private func act(on text: String) {
        if let c = confirmID {                          // D3: while a yes is wanted, only a clear yes runs it; anything else is a no
            guard confirmArmed else { return }          // the preview is still being read: a yes now is not an answer to it
            let yes = VoiceIntent.of(text, confirming: true) == .yes
            confirmID = nil; confirmArmed = false
            line = yes ? "Yes" : "No"
            state = .thinking
            Task { await chat.answer(turn: c.turn, yes: yes) }
            return
        }
        switch VoiceIntent.of(text, confirming: false) {
        case .hush:
            if state != .listening { bargeIn() }
            line = ""
        case .bye:
            route(text)                                 // in live mode "bye" is usually said to someone else
        case .yes, .no:
            guard let c = confirmID else { return }
            let yes = VoiceIntent.of(text, confirming: true) == .yes
            confirmID = nil
            line = yes ? "Yes" : "No"
            state = .thinking
            Task { await chat.answer(turn: c.turn, yes: yes) }
        case .say(let s):
            route(s)
        }
    }

    /// A phrase heard in live mode: is it for her, is it finished, and is she free to answer it?
    private func route(_ s: String) {
        let log = ambient != .off && !classMode
        guard VoiceIntent.isSpeech(s), !VoiceIntent.isLoneAck(s) else {   // "Uh." or a lone "Yeah." from room noise is not a message
            if log, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                thoughts.finish(thoughts.begin(s), .noise, "just a sound or a lone \"yeah\", skipped without asking anyone")
            }
            return
        }
        let tid: UUID? = log ? thoughts.begin(s) : nil
        if tid != nil { thoughts.step(tid, "Heard with \(EarsModel.current == .phonon2 ? "Phonon-2" : EarsModel.current == .fluidMini ? "Fluid Speech Mini" : "Phonon-2.5")") }
        if let on = VoiceIntent.classMode(s) {
            classMode = on
            line = on ? "Class mode on" : "Class mode off"
            if let c = classSink { Task { if on { await c.suggest(); await c.start() } else { await c.end() } } }
            return
        }
        if ambient != .off, VoiceIntent.ambientName(s, extra: extraNames), VoiceIntent.ambient(s) == false {
            line = ""
            thoughts.finish(tid, .answered, "you asked her to stop listening: always listening is off")
            Self.ambientWanted = .off
            return end()                                            // "Edith, stop listening for my name"
        }
        let m = sent
        let mergeable = m != nil && state == .thinking && !firstAudioLogged && Date().timeIntervalSince(m!.at) < Self.mergeWindow
        let followUp = state == .listening && Date().timeIntervalSince(spokeAt) < Self.followUp && spokeAt > .distantPast
        let choice = VoiceIntent.routeChoice(s, classMode: classMode, canMerge: mergeable, hasHeld: held != nil, followUp: followUp,
                                             ambient: ambient, extraNames: extraNames)
        if ambient != .off { VoiceLog.write("AMBIENT \(choice) words=\(VoiceIntent.words(s).count)") }
        if choice == .silent { return }                             // silent: nothing is sent anywhere
        if choice == .drop {
            thoughts.finish(tid, .ignored, "no \"Edith\" or \"Misty\" at the start or end, and Her name only mode is on")
            ambientDrops += 1; roomHeard.append(Date()); keep(s, outcome: "dropped", followUp: followUp); return
        }            // F5: not for her: no model is called, nothing is sent
        // he kept going after she started working on it, before she spoke: one message, asked again
        if choice == .merge, let m {
            thoughts.finish(tid, .answered, "you kept talking before she answered, so it was joined to your last message")
            VoiceLog.write("merged into the last message")
            keep(s, outcome: "merged", followUp: followUp)
            if VoiceIntent.endedEarly(earlyAt: ears.lastEarlyEnd, sentAt: m.at) { VoiceLog.write("EARLY_END merged=true") }
            stopTurn(m.turn)
            turn = nil
            return deliver(m.text + " " + s)
        }
        if let h = held {                                           // the rest of a sentence that stopped mid-thought
            thoughts.finish(tid, .answered, "the rest of a sentence you paused in, sent to her together")
            keep(s, outcome: "held", followUp: followUp)
            held = nil
            holdTask?.cancel()
            return deliver(h + " " + s)
        }
        if choice == .deliver {
            thoughts.finish(tid, .answered, followUp ? "said within 8 s of her last reply, so it's a follow-up: sent to her, no check needed"
                                                    : "her name was in it: sent to her, no check needed")
            keep(s, outcome: "delivered", followUp: followUp); return deliver(s)
        }
        if choice == .addressed { Task { await self.classifyAddressed(s, followUp: followUp, tid: tid) }; return }
        thoughts.step(tid, "Old live mode check (live.hear)")
        Task { await self.classify(s, followUp: followUp) }
    }

    /// F7: ambient `judge` mode. One engine call; only `answers == true` (the label `for_her`) reaches her. Unsure, not for her, a failed
    /// call or a reply that is not that shape is a drop, and no word of it is logged.
    private func classifyAddressed(_ s: String, followUp: Bool, tid: UUID? = nil) async {
        struct P: Encodable, Sendable { var text: String; var lastReply: String; var sinceReply: Double?; var named: Bool; var frame: JSONValue?
            var room: Int
            enum CodingKeys: String, CodingKey { case text, lastReply = "last_reply", sinceReply = "since_reply", named, frame, room } }
        struct R: Decodable, Sendable { var label: String; var answers: Bool; var why: String?; var wrote: Bool?; var app: String?; var text: String? }
        let since = spokeAt > .distantPast ? Date().timeIntervalSince(spokeAt) : nil
        let room = roomCount()
        let fr = frameHold.frame
        let app = fr?["app"]?.stringValue ?? "unknown app"
        let inBox = fr?["typing"] == .bool(true)
        thoughts.step(tid, "No name, so asking the judge (Haiku). Clues: \(app) in front, cursor \(inBox ? "in a text box" : "not in a text box"), "
                      + (room == 0 ? "room quiet" : "room talking (\(room) other phrase\(room == 1 ? "" : "s") in 30 s)")
                      + (since.map { $0 < 120 ? ", she spoke \(Int($0)) s ago" : "" } ?? ""))
        let p = P(text: s, lastReply: said.joined(separator: " "), sinceReply: since, named: false, frame: fr, room: room)
        let t0 = Date()
        let r: R? = try? await api.call("addressed.judge", p)
        let ms = Int(Date().timeIntervalSince(t0) * 1000)
        if let r {
            let why = (r.why ?? "").isEmpty ? "" : ": \(r.why!)"
            thoughts.step(tid, "Judge said \(r.label.replacingOccurrences(of: "_", with: " ")) in \(ms) ms\(r.label == "write" ? "" : why)")
        } else {
            thoughts.step(tid, "The judge didn't answer (\(ms) ms), so she stays quiet")
        }
        guard state != .idle, ambient == .judge else { thoughts.finish(tid, .ignored, "always listening was turned off meanwhile"); return }
        if let r, r.label == "write" {                           // dictation: the engine already typed it into his box
            VoiceLog.write("AMBIENT judge write wrote=\(r.wrote ?? false) room=\(room)")
            if r.wrote == true {
                line = "Typed into \(r.app ?? "your app")"; keep(s, outcome: "written", followUp: followUp)
                thoughts.finish(tid, .typed, "typed into \(r.app ?? "your app"), not sent: \"\(r.text ?? "")\"")
            } else {
                thoughts.finish(tid, .ignored, "meant to type it, but couldn't reach the app")
            }
            return
        }
        if let r, r.answers, r.label == "for_her" {
            VoiceLog.write("AMBIENT judge for_her room=\(room)")
            thoughts.finish(tid, .answered, "sent to Edith to answer or do")
            keep(s, outcome: "delivered", followUp: followUp)
            return deliver(s)
        }
        thoughts.finish(tid, .ignored, r?.label == "unsure" ? "not sure it was for her, so she stayed quiet (say \"Edith\" to be sure)"
                                                            : "not for her, so nothing happened")
        VoiceLog.write("AMBIENT judge \(r?.label ?? "none") room=\(room)")
        ambientDrops += 1
        roomHeard.append(Date())
        keep(s, outcome: "dropped", followUp: followUp)
    }

    /// F6: hand one heard final to the engine's local collector, which keeps it only if he turned `collect_phrases` on. Never in class
    /// mode, never with a private app in front, never anything but the words (the engine scrubs them). No audio, no model.
    private func keep(_ s: String, outcome: String, followUp: Bool) {
        guard !classMode else { return }
        if let b = frontBundle(), ContextFrame.privateBundles.contains(b) { return }
        let since = spokeAt > .distantPast ? Date().timeIntervalSince(spokeAt) : nil
        let row = PhraseNote(text: s, state: state == .speaking ? "speaking" : "listening", named: VoiceIntent.ambientName(s, extra: extraNames),
                             followup: followUp, sinceReply: since, outcome: outcome)
        Task { await self.collect(row) }
    }

    private func collect(_ row: PhraseNote) async {
        struct State: Decodable, Sendable { var collecting: Bool }
        struct Kept: Decodable, Sendable { var kept: Bool? }
        if Date().timeIntervalSince(collectingCheckedAt) > 30 {                 // the engine owns the switch; ask it, at most every 30 s
            collectingCheckedAt = Date()
            let r: State? = try? await api.call("phrases.state", Empty())
            collectingOn = r?.collecting ?? false
        }
        guard collectingOn else { return }                                      // off: the words never leave this process
        let _: Kept? = try? await api.call("phrases.add", row)
    }

    /// Not plainly for her: Jev decides (who it was for, finished or not). Unsure means it wasn't for her.
    private func classify(_ s: String, followUp: Bool) async {
        struct P: Encodable, Sendable { var text: String; var lastReply: String; var sinceReply: Double?
            enum CodingKeys: String, CodingKey { case text, lastReply = "last_reply", sinceReply = "since_reply" } }
        struct R: Decodable, Sendable { var to: String; var finished: Bool }
        let since = spokeAt > .distantPast ? Date().timeIntervalSince(spokeAt) : nil
        let r: R? = try? await api.call("live.hear", P(text: s, lastReply: said.joined(separator: " "), sinceReply: since))
        guard state != .idle, let r else { return }
        VoiceLog.write("hear to=\(r.to) finished=\(r.finished)")
        keep(s, outcome: r.to == "misty" ? (r.finished ? "delivered" : "held") : "dropped", followUp: followUp)
        guard r.to == "misty" else { return }
        if !r.finished { return hold(s) }
        deliver(s)
    }

    /// A message for her: wait out a sentence that stopped mid-thought, queue it while she's busy, else send it.
    private func deliver(_ s: String) {
        dim()                                                       // sent to her: the dot's job (she heard her name) is done
        if VoiceIntent.looksCutOff(s) { return hold(s) }
        if turn != nil && (state == .thinking || state == .speaking) {
            waiting.append(s)                                       // never cancels what she's doing
            VoiceLog.write("queued while busy")
            return
        }
        send(s)
    }

    private func hold(_ s: String) {
        held = s
        line = s
        holdTask?.cancel()
        holdTask = Task { [weak self] in
            var waited = 0.0
            while waited < Self.holdFor * 2.5 {                     // still talking: keep waiting, up to ~4 s
                try? await Task.sleep(for: .seconds(0.2))
                waited += 0.2
                guard let self, !Task.isCancelled else { return }
                if waited >= Self.holdFor && !self.ears.inSpeech { break }
            }
            guard let self, !Task.isCancelled, self.held == s else { return }
            self.held = nil
            VoiceLog.write("held sentence sent as is")
            if self.turn != nil { self.waiting.append(s) } else { self.send(s) }
        }
    }

    /// Captures his last app's frame in the background when speech starts; only the time is logged, never content.
    private func grabFrame() {
        guard frameHold.wantsCapture() else { return }
        Task { [chat] in
            let t0 = ContinuousClock.now
            let f = await chat.frameProvider?()
            let ms = Int((ContinuousClock.now - t0) / .milliseconds(1))
            VoiceLog.write("FRAME_CAPTURE ms=\(ms)")
            frameHold.store(f)
        }
    }

    private func send(_ text: String) {
        let t = "v" + UUID().uuidString.prefix(8).lowercased()
        turn = t
        splitter = SentenceSplitter()
        fed = ""
        replyDone = false
        firstAudioLogged = false
        cancelLinger()
        reply = ""
        line = text
        state = .thinking
        sent = (text, t, Date())
        tt.bind(t)
        tt.mark("send")
        Task {
            let held = frameHold.take()
            let reply = await chat.send(text, voice: true, turn: t, frame: held)
            guard turn == t else { return }
            // The answer can beat the last streamed piece to the app: say whatever of it hasn't been spoken yet.
            if speakReplies, let reply, let rest = ReplyTail.missing(shown: fed, reply: reply), !rest.isEmpty {
                VoiceLog.write("reply tail recovered: \(rest.count) chars")
                for s in splitter.feed(rest) { say(s) }
            }
            replyDone = true
            for s in splitter.flush() { say(s) }
            if !speaker.isSpeaking { spokeAll() }
        }
    }

    // MARK: speaking (events from the engine, routed by AppState)

    public func delta(turn t: String, text: String) {
        if t == turn { tt.mark("first_delta") }
        guard t == turn, speakReplies, !replyDone else { return }
        fed += text
        for s in splitter.feed(text) { say(s) }
    }

    /// The model has finished streaming (engine's chat.done): say the last sentence now instead of after the RPC reply.
    public func streamEnded(turn t: String) {
        guard t == turn, speakReplies, !replyDone else { return }
        replyDone = true
        for s in splitter.flush() { say(s) }
    }

    public func status(turn t: String, text: String) {
        guard t == turn, state == .thinking else { return }
        line = text
    }

    public func ask(turn t: String, id: String, question: String) {
        guard t == turn else { return }
        confirmID = (t, id)
        confirmArmed = false
        say(question + " Yes or no?")
    }

    private func say(_ sentence: String, caption: Bool = false) {
        guard !inClass else { return }                              // a class is silent, whatever asked
        if !caption, narrator.hold(sentence) { return }             // the reply waits for the drawing's captions
        guard state != .idle, state != .listening || turn != nil else { return }
        if tt.active, !tt.isPending { if !tt.hasMark("first_clause") { tt.firstClauseChars = sentence.count }; tt.mark("first_clause") }
        if !firstAudioLogged, let e = speechEnd { firstAudioLogged = true; log("first_audio_ms", e) }
        state = .speaking
        ears.setSpeaking(true)
        said = Array((said + [sentence]).suffix(4))
        line = sentence
        if !caption { cancelLinger(); reply = PillLayout.appended(reply, sentence) }
        speaker.speak(sentence)
    }

    private func spokeAll() {
        if narrator.active { narrator.spoke(); return }             // a caption finished, not the whole turn
        if updateLine != nil, state == .speaking { return finishUpdateLine() }
        guard state == .speaking || state == .thinking else { return }
        if Self.moreComing(talking: talking, replyDone: replyDone, asking: confirmID != nil) { return }   // "One sec." is not the whole answer
        spokeAt = Date()
        if talking {                                  // her turn is over: back to waiting for a tap
            if confirmID != nil { confirmArmed = true; quiet(); state = .listening; line = "Tap to answer"; return }
            turn = nil; sent = nil
            lingerThenHide = !reply.isEmpty
            return end()
        }
        Task { @MainActor in                          // keep the high bar through the echo tail
            try? await Task.sleep(for: .seconds(Self.echoTail))
            if self.state == .listening { self.ears.setSpeaking(false) }
        }
        echoOffSoon()
        ears.reset()                                  // a phrase that opened on her own echo is dropped
        if confirmID == nil { turn = nil; sent = nil }
        state = .listening
        line = ""
        if !reply.isEmpty { startLinger(hideAfter: false) }
        lastHeard = Date()
        if turn == nil, !waiting.isEmpty {                          // what he asked while she was busy, all at once
            let next = waiting.joined(separator: " ")
            waiting = []
            send(next)
        }
    }

    /// Timing only (card A3): ms from the summon key reaching Misty to the speaker stopped, plus the audio already
    /// handed to the hardware (it still plays out). Silence = key_to_stop_ms + out_ms.
    private static func keyToStop(_ outMs: Int) -> String {
        guard let k = SummonKey.lastFiredAt else { return "key_to_stop_ms=none out_ms=\(outMs)" }
        return "key_to_stop_ms=\(Int((ContinuousClock.now - k) / .milliseconds(1))) out_ms=\(outMs)"
    }

    private func quiet() {
        ears.setSpeaking(false)
        spokeAt = Date()
        echoOffSoon()
    }

    /// Back to the plain mic once her echo has died away, if she is only listening again (not for her own update line,
    /// which keeps its echo-cancelled mic until it closes).
    private func echoOffSoon() {
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(Self.echoTail + 0.3))
            if self.state == .listening, !self.classMode, self.updateLine == nil, self.audio.running { self.audio.setEchoCancel(false) }
        }
    }

    private func stopTurn(_ t: String) {
        struct P: Encodable, Sendable { var turn: String }
        Task { let _: JSONValue? = try? await api.call("chat.stop", P(turn: t)) }
    }

    private func log(_ what: String, _ since: ContinuousClock.Instant) {
        let ms = Int((ContinuousClock.now - since) / .milliseconds(1))
        NSLog("VOICE_TURN %@=%d", what, ms)
        VoiceLog.write("VOICE_TURN \(what)=\(ms)")
    }
}
#endif

extension Notification.Name {
    static let mistyClassWavDone = Notification.Name("misty.classWavDone")
}
