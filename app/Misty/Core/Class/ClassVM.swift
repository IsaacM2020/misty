import AppKit
import Foundation

/// A class session in the app. Misty's ears (live mode's mic, in class mode: silent, nothing goes to chat) hand each
/// finished phrase here, and it goes to the engine as a transcript segment, timed from the class start. Audio never
/// leaves the ears. Closing the lid pauses the class (the Mac sleeps and the mic stops), and 15 quiet minutes end it.
@MainActor
@Observable
public final class ClassVM {
    public enum State: Equatable, Sendable { case idle, warming, live, paused(String), ending }

    public private(set) var state: State = .idle
    public private(set) var session: ClassSession?
    public private(set) var segments: [ClassSegment] = []
    public private(set) var partialLine = ""
    public private(set) var partialSource = "room"
    public private(set) var macProblem: String?       // online class: the Mac's sound isn't coming through
    public private(set) var elapsed: TimeInterval = 0
    public private(set) var endedNote: String?
    public private(set) var problem: String?
    public var notesText = ""
    public var noteStatus: [String: String] = [:]       // class id -> where its notes are (from class.notes events)
    public internal(set) var list: [ClassSession] = []
    public internal(set) var detail: ClassDetail?
    public var subject = UserDefaults.standard.string(forKey: "misty.class.lastSubject") ?? "Chem"
    /// True once he chose the subject himself: the calendar's guess must not flip it back.
    public internal(set) var subjectPicked = false
    /// The calendar event he is in, if any. It names the class when it starts.
    var calTitle: String?
    public func pick(_ s: String) { subject = s; subjectPicked = true }
    public static let subjects = ["Chem", "Math", "Physics", "Econ", "English", "Spanish", "Other"]

    /// How the class is heard. Online: the Mac's sound is the teacher (any platform) and the mic is Isaac. In person: the mic.
    public enum Mode: String, Sendable, CaseIterable { case room, online }
    public var mode: Mode = .room
    public private(set) var hint: String?              // "Chem tutor is on your calendar with a video link"

    /// An online class about to start that Misty is asking about ("Physics starts in 2 min. Take notes?").
    public struct Ask: Equatable, Sendable {
        public var title: String; public var subject: String?; public var startsIn: Double
        public var detected = false                // a call noticed on the mic, not a calendar event
        public var online = true                   // a calendar class without a video link is in the room
    }
    public private(set) var ask: Ask?
    @ObservationIgnored public var onAsk: (@MainActor () -> Void)?            // shows the ask card
    @ObservationIgnored var watchEvery: Duration = .seconds(30)
    private var watcher: Task<Void, Never>?
    private var meetingWatcher: Task<Void, Never>?
    @ObservationIgnored var micUsers: @MainActor () -> [MicUser] = { MicUsers.current() }
    @ObservationIgnored var meetingEvery: Duration = .seconds(3)

    /// Turns the ears on (true) or off (false) in class mode; AppState wires it to VoiceVM. False back = no mic.
    @ObservationIgnored public var listen: (@MainActor (Bool, Mode) async -> Bool)?
    /// A note when the Mac's sound isn't arriving (permission off, tap failed, nothing played yet); nil when fine.
    @ObservationIgnored public var macCheck: (@MainActor () -> String?)?
    /// Online classes: the text of the shared screen when it shows a new slide (read on this Mac), else nil. AppState wires it.
    @ObservationIgnored public var slideSource: (@MainActor () async -> String?)?
    @ObservationIgnored public var slideReset: (@MainActor () -> Void)?
    public private(set) var slideCount = 0
    @ObservationIgnored var slideEvery = 10                                 // seconds between looks
    private var slideBusy = false
    /// Live transcript v2: every `correctEvery` seconds the engine is asked for one round of corrections (Phonon-2.5
    /// English re-reads a finished 45-90 s stretch of both tracks and the corrected lines replace the preview). The
    /// engine decides when enough has finished; one round at a time here too.
    @ObservationIgnored var correctEvery = 30
    private var correctBusy = false
    public private(set) var correctedUpTo: Double = 0                      // class seconds already corrected
    private var ticks = 0
    @ObservationIgnored public var onStarted: (@MainActor () -> Void)?       // shows the side panel
    @ObservationIgnored public var onEnded: (@MainActor (String) -> Void)?   // the class id, once its notes are on the way
    @ObservationIgnored var saveDelay: Duration = .seconds(1)

    static let quietEnd: TimeInterval = 15 * 60
    private let api: EngineAPI
    private let now: () -> Date
    private var phraseStarts: [String: TimeInterval] = [:]
    private var lastHeard = Date()
    private var saveTask: Task<Void, Never>?
    private var ticker: Task<Void, Never>?
    private var observers: [NSObjectProtocol] = []

    public init(api: EngineAPI, now: @escaping () -> Date = Date.init) {
        self.api = api
        self.now = now
    }

    public var isOpen: Bool { session != nil && state != .idle }
    private var since: TimeInterval { now().timeIntervalSince1970 - (session?.startedAt ?? now().timeIntervalSince1970) }

    // MARK: session

    /// Before a class: ask the calendar what he is in. A video link means online; the title picks the subject.
    public func suggest() async {
        guard !isOpen else { return }
        struct P: Encodable, Sendable {}
        struct R: Decodable, Sendable { var found: Bool; var title: String?; var online: Bool?; var subject: String? }
        guard let r: R = try? await api.call("class.now", P(), timeout: .seconds(8)), r.found else { hint = nil; calTitle = nil; return }
        mode = r.online == true ? .online : .room
        calTitle = r.title
        if !subjectPicked, let s = r.subject, Self.subjects.contains(s) { subject = s }
        hint = "\(r.title ?? "A class") is on your calendar" + (r.online == true ? " with a video link" : "")
    }

    /// Looks at the calendar every 30 s while the app runs, and asks once per online class just before it starts.
    public func watchCalendar() {
        guard watcher == nil else { return }
        watcher = Task { [weak self] in
            while !Task.isCancelled {
                await self?.checkSoon()
                try? await Task.sleep(for: self?.watchEvery ?? .seconds(30))
            }
        }
    }

    /// The engine decides (class.now `suggest`): auto = recorded before, start it; ask = never recorded, ask once;
    /// none = leave it. The engine remembers the event, so a class is never started or asked about twice.
    func checkSoon() async {
        guard !isOpen, ask == nil, state != .warming else { return }
        struct P: Encodable, Sendable {}
        struct R: Decodable, Sendable {
            var found: Bool; var title: String?; var online: Bool?; var subject: String?
            var startsIn: Double?; var key: String?; var isClass: Bool?; var suggest: String?
        }
        guard let r: R = try? await api.call("class.now", P(), timeout: .seconds(8)), r.found, r.isClass == true,
              let how = r.suggest, how == "auto" || how == "ask", let key = r.key, let t = r.startsIn else { return }
        var asked = UserDefaults.standard.stringArray(forKey: "misty.class.asked") ?? []
        guard !asked.contains(key), !isOpen else { return }
        asked = Array((asked + [key]).suffix(30))
        UserDefaults.standard.set(asked, forKey: "misty.class.asked")
        struct Reach: Encodable, Sendable { var key: String; var title: String; var suggest: String }
        let title = r.title ?? "Your class"
        let _: JSONValue? = try? await api.call("class.reach", Reach(key: key, title: title, suggest: how), timeout: .seconds(8))
        calTitle = r.title
        let subj = r.subject.flatMap { Self.subjects.contains($0) ? $0 : nil }
        if how == "auto" {
            mode = r.online == true ? .online : .room
            await start(subject: subj ?? subject)
        } else {
            var a = Ask(title: title, subject: subj, startsIn: t)
            a.online = r.online == true
            showAsk(a)
        }
    }

    /// Every 3 s: is a call app on the mic? One "take notes?" per call, never while a class is already running.
    public func watchMeetings() {
        guard meetingWatcher == nil else { return }
        meetingWatcher = Task { [weak self] in
            var watch = MeetingWatch()
            while !Task.isCancelled {
                guard let self else { return }
                if let app = watch.step(app: MeetingApps.inCall(self.micUsers()), now: self.now()), !self.isOpen {
                    await self.meetingDetected(app)
                }
                try? await Task.sleep(for: self.meetingEvery)
            }
        }
    }

    /// A call app has had the mic for a while (Granola-style). Asks "take notes?" unless a class is already running or
    /// the card is up. The subject comes from a class on the calendar right now, else the last one he used.
    public func meetingDetected(_ app: String) async {
        guard !isOpen, ask == nil, state != .warming else { return }
        struct P: Encodable, Sendable {}
        struct R: Decodable, Sendable { var found: Bool; var subject: String?; var title: String? }
        let r: R? = try? await api.call("class.now", P(), timeout: .seconds(8))
        guard !isOpen, ask == nil else { return }
        calTitle = r.flatMap { $0.found ? $0.title : nil }
        let subj = r.flatMap { $0.found ? $0.subject : nil }.flatMap { Self.subjects.contains($0) ? $0 : nil }
        showAsk(Ask(title: "\(app) call", subject: subj, startsIn: 0, detected: true))
    }

    func showAsk(_ a: Ask) {
        ask = a
        onAsk?()
    }

    /// Yes on the ask card: an online class, subject from the calendar.
    public func takeNotes() async {
        guard let a = ask else { return }
        ask = nil
        mode = a.online ? .online : .room
        await start(subject: a.subject ?? subject)
    }

    public func skipAsk() { ask = nil }

    // MARK: started by the engine ("start my class" by voice or chat: the class_start move)

    /// What the app answers the engine's `class_start` request with.
    public enum EngineStart: Equatable, Sendable { case started, already, queued, failed(String) }

    /// True while a voice turn is running (heard, thinking, speaking her reply). Starting a class takes the mic and ends the
    /// voice session, which would cut that reply off, so the start waits. AppState wires it to VoiceVM.
    @ObservationIgnored public var voiceBusy: (@MainActor () -> Bool)?
    @ObservationIgnored var busyPoll: Duration = .milliseconds(250)
    @ObservationIgnored var busyLimit: Duration = .seconds(90)

    /// He asked her to start the class. The same as Yes on the ask card (and the engine's `auto`): the calendar event names it,
    /// the engine's mode and subject are used, the ask card goes away and the event is never asked about again. In a voice
    /// turn it answers `.queued` at once and starts when her reply is done; otherwise it starts now and answers what happened.
    public func startFromEngine(title: String, subject: String, online: Bool, key: String) async -> EngineStart {
        if isOpen || state == .warming { return .already }
        ask = nil
        if !key.isEmpty {
            let asked = UserDefaults.standard.stringArray(forKey: "misty.class.asked") ?? []
            if !asked.contains(key) { UserDefaults.standard.set(Array((asked + [key]).suffix(30)), forKey: "misty.class.asked") }
        }
        if voiceBusy?() == true {
            Task { [weak self] in await self?.startWhenVoiceIsFree(title: title, subject: subject, online: online) }
            return .queued
        }
        return await launchForEngine(title: title, subject: subject, online: online)
    }

    private func startWhenVoiceIsFree(title: String, subject: String, online: Bool) async {
        let deadline = ContinuousClock.now + busyLimit
        while voiceBusy?() == true, ContinuousClock.now < deadline { try? await Task.sleep(for: busyPoll) }
        _ = await launchForEngine(title: title, subject: subject, online: online)
    }

    private func launchForEngine(title: String, subject: String, online: Bool) async -> EngineStart {
        if isOpen || state == .warming { return .already }
        calTitle = title.isEmpty ? nil : title
        mode = online ? .online : .room
        await start(subject: Self.subjects.contains(subject) ? subject : "Other")
        if state == .live { return .started }
        return .failed(problem ?? "the class didn't start")
    }

    public func start(subject chosen: String? = nil) async {
        guard !isOpen else { return }
        ask = nil
        let subj = chosen ?? subject
        subject = subj
        UserDefaults.standard.set(subj, forKey: "misty.class.lastSubject")
        problem = nil
        endedNote = nil
        state = .warming
        detail = nil
        onStarted?()
        struct P: Encodable, Sendable { var subject: String; var mode: String; var cal_title: String? }
        let named = calTitle
        calTitle = nil
        subjectPicked = false
        do {
            session = try await api.call("class.start", P(subject: subj, mode: mode.rawValue, cal_title: named))
        } catch {
            problem = "Couldn't start the class"
            state = .idle
            return
        }
        segments = []
        notesText = ""
        partialLine = ""
        slideCount = 0
        ticks = 0
        correctedUpTo = 0
        slideReset?()
        lastHeard = now()
        guard await listen?(true, mode) ?? false else {
            problem = "Mic is off: Settings > Permissions"
            await end()
            return
        }
        state = .live
        watchSleep()
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                await self?.tick()
            }
        }
    }

    /// Once a second: the class timer, and the end after 15 quiet minutes.
    func tick() async {
        guard isOpen else { return }
        elapsed = since
        macProblem = mode == .online ? macCheck?() : nil
        ticks += 1
        if mode == .online, state == .live, ticks % slideEvery == 0, !slideBusy { Task { await pollSlide() } }
        if mode == .online, state == .live, ticks % correctEvery == 0, !correctBusy { Task { await correctNow() } }
        if state == .live && now().timeIntervalSince(lastHeard) > Self.quietEnd {
            await end()
            endedNote = "Class ended (nothing heard for 15 min)"
        }
    }

    /// Looks at the shared screen once; a new slide's text goes to the engine for the notes.
    func pollSlide() async {
        guard let s = session, state == .live, mode == .online, let src = slideSource, !slideBusy else { return }
        slideBusy = true
        defer { slideBusy = false }
        guard let text = await src() else { return }
        struct P: Encodable, Sendable { var session: String; var t: Double; var text: String }
        struct R: Decodable, Sendable { var id: Int? }
        let r: R? = try? await api.call("class.slide", P(session: s.id, t: since, text: text))
        if r?.id != nil { slideCount += 1 }
    }

    /// One round of corrections. Skipped while the last one is still running; a failed round changes nothing.
    func correctNow() async {
        guard let s = session, state == .live, mode == .online, !correctBusy else { return }
        correctBusy = true
        defer { correctBusy = false }
        struct P: Encodable, Sendable { var session: String }
        guard let r: ClassCorrection = try? await api.call("class.correct", P(session: s.id), timeout: .seconds(300)),
              r.busy != true, session?.id == s.id else { return }
        segments = ClassLines.apply(segments, r.changes)
        correctedUpTo = max(correctedUpTo, r.upTo)
    }

    public func pause(reason: String = "paused") async {
        guard let s = session, state == .live else { return }
        state = .paused(reason)
        _ = await listen?(false, mode)
        struct P: Encodable, Sendable { var session: String; var reason: String }
        let _: JSONValue? = try? await api.call("class.pause", P(session: s.id, reason: reason))
    }

    public func resume() async {
        guard let s = session, case .paused = state else { return }
        struct P: Encodable, Sendable { var session: String }
        let _: JSONValue? = try? await api.call("class.resume", P(session: s.id))
        lastHeard = now()
        state = (await listen?(true, mode) ?? false) ? .live : .paused("mic")
    }

    public func end() async {
        guard let s = session, state != .idle, state != .ending else { return }
        state = .ending
        ticker?.cancel()
        stopWatchingSleep()
        _ = await listen?(false, mode)
        flushNotes(s.id)
        struct P: Encodable, Sendable { var session: String }
        let ended: ClassSession? = try? await api.call("class.end", P(session: s.id))
        session = nil
        segments = []
        partialLine = ""
        phraseStarts = [:]
        macProblem = nil
        elapsed = 0
        state = .idle
        await open(ended?.id ?? s.id)                    // the panel shows this class's notes as they're made
        onEnded?(ended?.id ?? s.id)
    }

    // MARK: hearing (from VoiceVM in class mode)

    public func partial(_ p: String, source: String = "room") {
        guard state == .live else { return }
        if phraseStarts[source] == nil { phraseStarts[source] = since }
        partialLine = p
        partialSource = source
    }

    public func heard(_ text: String, source: String = "room") async {
        guard let s = session, state == .live || state == .ending else { return }   // ending: the phrase in flight
        let t1 = since
        let words = Double(text.split(separator: " ").count)
        let t0 = min(phraseStarts[source] ?? max(0, t1 - words * 0.4), t1)
        phraseStarts[source] = nil
        if partialSource == source { partialLine = "" }
        lastHeard = now()
        if ClassLines.isCovered(tStart: t0, tEnd: t1, upTo: correctedUpTo) { return }   // already corrected: no second copy
        struct P: Encodable, Sendable { var session: String; var tStart: Double; var tEnd: Double; var source: String; var text: String }
        struct R: Decodable, Sendable { var id: Int? }
        let r: R? = try? await api.call("class.segment", P(session: s.id, tStart: t0, tEnd: t1, source: source, text: text))
        if let id = r?.id { segments.append(ClassSegment(id: id, tStart: t0, tEnd: t1, source: source, text: text)) }
    }

    // MARK: his notes

    /// Each line he starts gets the class time in front ("[12:34] ..."), so the notes can be matched to the class.
    public func stamped(_ text: String) -> String {
        let stamp = "[\(ClassTime.mmss(since))] "
        return text.components(separatedBy: "\n").map { line in
            if line.isEmpty || line.range(of: #"^\[\d{1,3}:\d{2}\] "#, options: .regularExpression) != nil { return line }
            return stamp + line
        }.joined(separator: "\n")
    }

    public func notesChanged(_ text: String) {
        notesText = text
        guard let s = session else { return }
        saveTask?.cancel()
        let delay = saveDelay
        saveTask = Task { [weak self] in
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled else { return }
            await self?.save(s.id, text)
        }
    }

    private func flushNotes(_ sid: String) {
        saveTask?.cancel()
        let text = notesText
        Task { await self.save(sid, text) }
    }

    private func save(_ sid: String, _ text: String) async {
        struct P: Encodable, Sendable { var session: String; var text: String }
        let _: JSONValue? = try? await api.call("class.notes.save", P(session: sid, text: text))
    }

    // MARK: ask

    public func ask(_ q: String, session sid: String? = nil, subject: String? = nil) async -> ClassAnswer {
        struct P: Encodable, Sendable { var question: String; var session: String?; var subject: String? }
        let fallback = ClassAnswer(answer: "Couldn't ask right now.", cites: [])
        return (try? await api.call("class.ask", P(question: q, session: sid ?? session?.id, subject: subject),
                                    timeout: .seconds(60))) ?? fallback
    }

    // MARK: lid

    func systemWillSleep() async { await pause(reason: "lid") }

    func systemDidWake() async {
        if case .paused("lid") = state { await resume() }
    }

    private func watchSleep() {
        let nc = NSWorkspace.shared.notificationCenter
        observers = [
            nc.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in await self?.systemWillSleep() }
            },
            nc.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in await self?.systemDidWake() }
            },
        ]
    }

    private func stopWatchingSleep() {
        observers.forEach { NSWorkspace.shared.notificationCenter.removeObserver($0) }
        observers = []
    }
}

// MARK: past classes (the Classes tab, and the notes shown after a class)

extension ClassVM {
    public func closeDetail() { detail = nil }

    public func loadList() async {
        struct R: Decodable, Sendable { var classes: [ClassSession] }
        struct P: Encodable, Sendable {}
        if let r: R = try? await api.call("class.list", P()) { list = r.classes }
    }

    public func open(_ id: String) async {
        struct P: Encodable, Sendable { var session: String }
        detail = try? await api.call("class.get", P(session: id))
    }

    /// A class.notes event: refresh whatever shows that class.
    public func notesMoved(_ id: String, _ status: String) async {
        noteStatus[id] = status
        if detail?.session.id == id { await open(id) }
        await loadList()
    }

    public func regenerate(_ id: String) async {
        struct P: Encodable, Sendable { var session: String }
        let _: JSONValue? = try? await api.call("class.regenerate", P(session: id))
        noteStatus[id] = "noting"
    }

    /// His own name or subject for a class that already happened.
    public func rename(_ id: String, title: String?, subject: String?) async {
        struct P: Encodable, Sendable { var session: String; var title: String?; var subject: String? }
        let _: JSONValue? = try? await api.call("class.rename", P(session: id, title: title, subject: subject))
        if detail?.session.id == id { await open(id) }
        await loadList()
    }

    public func delete(_ id: String) async {
        struct P: Encodable, Sendable { var session: String }
        let _: JSONValue? = try? await api.call("class.delete", P(session: id))
        if detail?.session.id == id { detail = nil }
        await loadList()
    }

    /// Delete several classes at once (transcript, notes, action items, teacher links, merged parts; audio to the Trash).
    public func deleteMany(_ ids: [String]) async {
        struct P: Encodable, Sendable { var sessions: [String] }
        let _: JSONValue? = try? await api.call("class.delete", P(sessions: ids))
        if let d = detail?.session.id, ids.contains(d) { detail = nil }
        await loadList()
    }

    /// Merge two or more classes into the earliest one, in time order. `title` renames the result. Returns its id.
    @discardableResult
    public func merge(_ ids: [String], title: String?) async -> String? {
        struct P: Encodable, Sendable { var sessions: [String]; var title: String? }
        struct R: Decodable, Sendable { var kept: String }
        let r: R? = try? await api.call("class.merge", P(sessions: ids, title: title))
        guard let kept = r?.kept else { return nil }
        noteStatus[kept] = "noting"
        await loadList()
        await open(kept)
        return kept
    }

    public func setActionDone(_ action: String, _ done: Bool) async {
        struct P: Encodable, Sendable { var action: String; var done: Bool }
        let _: JSONValue? = try? await api.call("class.action.done", P(action: action, done: done))
        if let id = detail?.session.id { await open(id) }
    }

    /// The notes as Markdown, for Copy.
    public static func markdown(_ d: ClassDetail) -> String {
        var out = ["# \(d.session.title)"]
        guard let n = d.session.notes else { return out[0] }
        out += ["", n.summary]
        for s in n.sections { out += ["", "## \(s.heading)"] + s.points.map { "- \($0.text)" } }
        if !n.remember.isEmpty { out += ["", "## Remember"] + n.remember.map { "- \($0.text)" } }
        if !n.actionItems.isEmpty {
            out += ["", "## Action items"] + n.actionItems.map { "- [ ] \($0.text)" + ($0.due.map { " (due \($0))" } ?? "") }
        }
        if let lost = n.lost, !lost.isEmpty { out += ["", "## Where you got lost"] + lost.map { "- \($0.text)" } }
        if let step = n.nextStep { out += ["", "## Next step", step.text] }
        return out.joined(separator: "\n")
    }
}

/// The answer to the engine's `class_start` request: always a good wire reply, with the outcome inside, so the engine can tell
/// "the app said no (mic off)" from "the app did not answer".
enum ClassStartReply {
    static func make(_ r: ClassVM.EngineStart) -> HandsResult {
        switch r {
        case .started: return .good(["ok": .bool(true), "status": .string("started")])
        case .already: return .good(["ok": .bool(true), "status": .string("already")])
        case .queued: return .good(["ok": .bool(true), "status": .string("queued")])
        case .failed(let why): return .good(["ok": .bool(false), "status": .string("failed"), "problem": .string(why)])
        }
    }
}
