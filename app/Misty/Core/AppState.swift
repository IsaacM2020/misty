import Foundation
import Observation
import Network
#if canImport(AppKit)
import AppKit
#endif
import ServiceManagement

@MainActor
@Observable
public final class AppState {
    public private(set) var engineState: EngineState = .stopped
    public private(set) var status: Status?
    public private(set) var strip: StripModel = StripModel.make(status: nil, engine: .stopped)
    public var setupComplete: Bool {
        didSet { UserDefaults.standard.set(setupComplete, forKey: "misty.setupComplete") }
    }
    public var launchAtLogin: Bool = false
    public var lastError: String?
    /// While the Google sign-in is open in the browser ("waiting"), or what went wrong the last time.
    public var googleNote: String?
    /// A tab the main window should switch to (a Tab raw value; Core can't see Views). The menu bar's "Settings…".
    public var wantTab: String?
    /// V1: bumped when something (the voice pill) wants the main window in front.
    public var openMainRequest = 0

    public let live = LiveAPI()
    public let suggestions: SuggestionsVM
    public let priorities: PrioritiesVM
    public let radar: RadarVM
    public let chat: ChatVM
    public let permissions = PermissionsVM()
    public let voice: VoiceVM
    public let dictation: DictationVM
    public let classes: ClassVM
    public let slate = SlateVM()
    public var onSlate: (() -> Void)?
    private var summon: SummonKey?
    /// "Edith" said out loud (the wake word). Starts the voice session the hotkey starts.
    public let wake: WakeListener
    @ObservationIgnored private var callCheck: (at: Date, app: String?) = (.distantPast, nil)
    private var supervisor: EngineSupervisor?
    private var eventTask: Task<Void, Never>?
    private var connectTask: Task<Void, Never>?
    private var presenceTask: Task<Void, Never>?
    @ObservationIgnored private var wakeObserver: NSObjectProtocol?
    private var monitor: NWPathMonitor?
    private var online = true
    private var client: RpcClient?
    private let handsProvider: AXProvider
    private var activityNoter: ActivityNoter?
    private var hands: HandsBridge?
    private var inputTap: UserInputTap?
    public let home: String
    public let socketPath: String

    public init(home: String = ProcessInfo.processInfo.environment["MISTY_HOME"] ?? (NSHomeDirectory() + "/Misty"),
                hands: AXProvider = SystemHands()) {
        self.home = home
        self.handsProvider = hands
        self.socketPath = home + "/run/engine.sock"
        self.setupComplete = UserDefaults.standard.bool(forKey: "misty.setupComplete")
        self.suggestions = SuggestionsVM(api: live)
        self.priorities = PrioritiesVM(api: live)
        self.radar = RadarVM(api: live)
        let chatVM = ChatVM(api: live)
        let frames = HandsService(provider: hands)
        chatVM.frameProvider = { let r = await frames.handle(op: "context_frame", args: .object([:])); return r.ok ? r.data : nil }
        self.chat = chatVM
        let voice = VoiceVM(api: live, chat: chat)
        self.voice = voice
        self.dictation = DictationVM(ears: voice.ears)
        self.classes = ClassVM(api: live)
        voice.classSink = classes
        classes.listen = { [weak voice, weak classes] on, mode in
            var pausing = false                      // a lid-close pause: the recording carries on in the same files
            if case .paused = classes?.state { pausing = true }
            return await voice?.listenForClass(on, online: mode == .online, session: classes?.session?.id, pausing: pausing) ?? false
        }
        classes.voiceBusy = { [weak voice] in      // a voice turn is on: "start my class" waits until her reply is done
            guard let s = voice?.state else { return false }
            return s == .starting || s == .thinking || s == .speaking
        }
        classes.macCheck = { [weak voice] in voice?.macAudioNote() }
        let slides = SlideWatcher()
        classes.slideSource = { await slides.poll() }
        classes.slideReset = { Task { await slides.reset() } }
        self.wake = WakeListener(home: home)
        self.launchAtLogin = SMAppService.mainApp.status == .enabled
        WakeWiring.connect(listener: wake, voice: voice) { [weak self] in self?.wakeContext() ?? WakeGate() }
    }

    /// What only the app knows, for the wake gate: paused, a class, a call app on the mic, dictation, the mic permission.
    func wakeContext() -> WakeGate {
        var g = WakeGate()
        g.paused = status?.paused ?? false
        g.inClass = classes.isOpen || classes.state == .warming
        g.dictating = dictation.recording
        g.micAllowed = AudioIO.micAllowed
        if Date().timeIntervalSince(callCheck.at) > 5 {                  // asking CoreAudio who has the mic: every 5 s is plenty
            callCheck = (Date(), MeetingApps.inCall(MicUsers.current()))
        }
        g.inCall = callCheck.app != nil
        return g
    }

    // MARK: the wake word

    /// Read the `wake_word` setting from the engine and apply it. Until this has run the listener stays off (so a launch never
    /// listens before the engine has said whether he turned it off).
    public func refreshWake() async {
        struct R: Decodable, Sendable { var enabled: Bool; var word: String; var sensitivity: Double }
        guard let r: R = try? await live.call("wake.get", Empty()) else { return }
        wake.apply(WakeSettings(enabled: r.enabled, word: r.word, sensitivity: r.sensitivity))
        wake.start()
    }

    /// The menu or Settings switch: remember it in the engine and apply it now.
    public func setWakeEnabled(_ on: Bool) {
        var s = wake.settings
        s.enabled = on
        wake.apply(s)
        struct P: Encodable, Sendable { var enabled: Bool }
        Task { let _: JSONValue? = try? await live.call("wake.set", P(enabled: on)) }
    }

    // MARK: lifecycle

    public func start() {
        guard supervisor == nil else { return }
        try? FileManager.default.createDirectory(atPath: home + "/run", withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let helpers = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/engine")   // Python tree lives in Resources: codesign seals it as files, not nested bundles
        let launcher = RealLauncher(socketPath: socketPath, mistyHome: home, helpersDir: helpers)
        let sup = EngineSupervisor(launcher: launcher) { [weak self] state in
            Task { @MainActor in self?.engineChanged(state) }
        }
        supervisor = sup
        LastApp.shared.start()
        let noter = ActivityNoter(api: live)
        noter.start()
        activityNoter = noter
        voice.onOpenChat = { [weak self] in self?.wantTab = "chat"; self?.openMainRequest += 1 }
        let key = SummonKey { [weak self] in Task { await self?.voice.tap() } }
        key.start()
        summon = key
        dictation.apply()
        if let secs = ProcessInfo.processInfo.environment["MISTY_DICTATE_MIC"].flatMap(Double.init) {
            dictation.testFromMic(seconds: secs, after: Double(ProcessInfo.processInfo.environment["MISTY_DICTATE_AFTER"] ?? "") ?? 20)
        }
        if let wav = ProcessInfo.processInfo.environment["MISTY_DICTATE_WAV"] {
            dictation.testFromFile(wav, after: Double(ProcessInfo.processInfo.environment["MISTY_DICTATE_AFTER"] ?? "") ?? 20)
        }
        Task {
            await voice.warm()
            // the live-mode mic test: MISTY_LIVE_TEST=<seconds> turns live mode on after MISTY_DICTATE_AFTER and off again
            if let secs = ProcessInfo.processInfo.environment["MISTY_LIVE_TEST"].flatMap(Double.init) {
                try? await Task.sleep(for: .seconds(Double(ProcessInfo.processInfo.environment["MISTY_DICTATE_AFTER"] ?? "") ?? 20))
                for _ in 0..<2 {                              // twice: voice processing goes off and on again between sessions
                    await voice.toggle()
                    try? await Task.sleep(for: .seconds(secs))
                    await voice.toggle()
                    try? await Task.sleep(for: .seconds(2))
                }
            }
            // the tap-to-talk test: MISTY_TALK_TEST=<seconds to talk> taps in after MISTY_DICTATE_AFTER, taps to send, lets
            // her speak for 6 s, taps to cut her off (and listen), then taps again to cancel
            if let secs = ProcessInfo.processInfo.environment["MISTY_TALK_TEST"].flatMap(Double.init) {
                try? await Task.sleep(for: .seconds(Double(ProcessInfo.processInfo.environment["MISTY_DICTATE_AFTER"] ?? "") ?? 20))
                await voice.tap()
                try? await Task.sleep(for: .seconds(secs))
                await voice.tap()
                try? await Task.sleep(for: .seconds(14))
                await voice.tap()
                try? await Task.sleep(for: .seconds(1.5))
                await voice.tap()
            }
            // the silent class test: MISTY_CLASS_AUTOSTART=<subject> with MISTY_CLASS_WAV starts a class, ends it after
            if let subj = ProcessInfo.processInfo.environment["MISTY_CLASS_AUTOSTART"],
               ProcessInfo.processInfo.environment["MISTY_CLASS_WAV"] != nil {
                NotificationCenter.default.addObserver(forName: .mistyClassWavDone, object: nil, queue: .main) { [weak self] _ in
                    Task { @MainActor in await self?.classes.end() }
                }
                await classes.start(subject: subj)
            }
        }
        Task { await sup.start() }
        classes.watchCalendar()                // asks "take notes?" just before an online class
        classes.watchMeetings()                // and when a call app takes the mic (Granola-style)
        // the ask card test: MISTY_ASK_TEST=<title> shows it after 8 s without the calendar
        if let t = ProcessInfo.processInfo.environment["MISTY_ASK_TEST"] {
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(8))
                self?.classes.showAsk(.init(title: t, subject: "Physics", startsIn: 120))
            }
        }
        startNetworkMonitor()
        startPresence()
        watchWake()
        // the proactive voice test: MISTY_SPEAKUP_TEST=<sentence> says it aloud 8 s after launch, as if she spoke up on her own
        if let t = ProcessInfo.processInfo.environment["MISTY_SPEAKUP_TEST"] {
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(8))
                self?.voice.speakUpdate(t)
            }
        }
    }

    /// After the Mac wakes: tell the engine he is here again, and log how long until the engine answers (Season 3 Day 10).
    private func watchWake() {
        #if canImport(AppKit)
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                let t0 = ContinuousClock.now
                await self.pushPresence()
                await self.refreshStatus()
                let d = ContinuousClock.now - t0
                VoiceLog.write("WAKE_READY engine_ms=\(Int(d.components.seconds * 1000 + d.components.attoseconds / 1_000_000_000_000_000)) state=\(self.engineState)")
            }
        }
        #endif
    }

    /// Every few seconds tell the engine how busy he is, so the gate can wait for a break before she speaks up.
    private func startPresence() {
        presenceTask?.cancel()
        presenceTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pushPresence()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    private func pushPresence() async {
        guard engineState == .running else { return }
        struct P: Encodable, Sendable { var idle_s: Double; var voice: String }
        #if canImport(AppKit)
        let idle = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: CGEventType(rawValue: ~0)!)
        #else
        let idle = 0.0
        #endif
        let _: JSONValue? = try? await live.call("presence.set", P(idle_s: idle, voice: voice.presenceVoice))
    }

    public func stop() {
        wake.stop()
        presenceTask?.cancel()
        monitor?.cancel()
        eventTask?.cancel()
        connectTask?.cancel()
        let sup = supervisor
        Task { await sup?.stop() }
        supervisor = nil
    }

    private func engineChanged(_ s: EngineState) {
        engineState = s
        if s == .running {
            connectTask?.cancel()
            connectTask = Task { await connectWhenReady() }
        } else {
            live.set(nil)
            client = nil
            inputTap?.stop()
            inputTap = nil
            hands = nil
            eventTask?.cancel()
        }
        refreshStrip()
    }

    private func connectWhenReady() async {
        var connected: RpcClient?
        for _ in 0..<150 {                               // up to ~30 s: a socket file left by a killed engine refuses until the new one listens
            if Task.isCancelled { return }
            if FileManager.default.fileExists(atPath: socketPath) {
                let c = RpcClient(socketPath: URL(fileURLWithPath: socketPath))
                if (try? await c.connect()) != nil { connected = c; break }
            }
            try? await Task.sleep(for: .milliseconds(200))
        }
        guard let c = connected else { return }
        client = c
        live.set(c)
        eventTask?.cancel()
        eventTask = Task { [weak self] in
            for await ev in c.events { await self?.handle(ev) }
        }
        let bridge = HandsBridge(api: live, service: HandsService(provider: handsProvider))
        hands = bridge
        await bridge.register()
        let tap = UserInputTap { ts in Task { await bridge.userInput(ts: ts) } }
        if tap.start() { inputTap = tap }
        await refreshStatus()
        await pushNetworkState()
        await suggestions.load()
        await priorities.load()
        await chat.load()
        await chat.loadFacts()
        await refreshWake()
    }

    func handle(_ ev: RpcEvent) async {
        switch ev {
        case .statusChanged(let s): status = s; refreshStrip()
        case .suggestionNew: await suggestions.load()
        case .suggestionProgress(let id, let text): suggestions.progress[id] = text
        case .buildProgress(let b):
            if var s = status { s.build = b; status = s; refreshStrip() }
        case .handsRequest(let reqId, let op, let args):
            let bridge = hands ?? HandsBridge(api: live, service: HandsService(provider: handsProvider))
            if op == "setting_apply" {                       // the engine's `setting` tool: the app changes its own setting
                let r = await SettingApply.run(name: args["name"]?.stringValue ?? "", value: args["value"] ?? .null, on: voice)
                await bridge.reply(reqId: reqId, r)
            } else if op == "class_start" {                  // the engine's `class_start` move: he said "start my class"
                var online = false
                if case .bool(let b)? = args["online"] { online = b }
                let r = await classes.startFromEngine(title: args["title"]?.stringValue ?? "", subject: args["subject"]?.stringValue ?? "",
                                                      online: online, key: args["key"]?.stringValue ?? "")
                await bridge.reply(reqId: reqId, ClassStartReply.make(r))
            } else {
                await bridge.handle(reqId: reqId, op: op, args: args)
            }
        case .chatDelta(let t, let text): chat.delta(turn: t, text: text); voice.delta(turn: t, text: text)
        case .chatStatus(let t, let text): chat.status(turn: t, text: text); voice.status(turn: t, text: text)
        case .chatDone(let t): voice.streamEnded(turn: t)
        case .chatConfirm(let t, let id, let q): chat.ask(turn: t, id: id, question: q); voice.ask(turn: t, id: id, question: q)
        case .proactive(let id, let how, let text):
            await chat.load()                               // her line is already saved in the chat
            if how != "card" { voice.speakUpdate(text, toldID: id) }    // a card is silent: he is working, or it is a note
        case .classNotes(let s, let st): await classes.notesMoved(s, st)
        case .slateShow(let j):
            guard let says = slate.present(j) else { break }
            onSlate?()                                      // the window opens; the page waits for its load before it inks
            if voice.canNarrate {                           // he talked to her: each line inks in as she says it
                voice.narrate(says, show: { _ in }, timing: slate.timing)   // the speaker's real audio times drive each line
            } else {
                slate.playSilently()
            }
        case .slateAppend(let j):                           // a lesson goes on: only the new steps are written and said
            guard let (from, says) = slate.append(j) else { break }
            if voice.canNarrate { voice.narrate(says, show: { _ in }, timing: slate.timing(offset: from)) }
            else { slate.playSilently(from: from) }
        case .slateUpdate(let b, let i, let t): slate.update(block: b, item: i, text: t)
        case .slateClose: slate.finish()
        case .slateHold: slate.holdOpen()
        case .unknown: break
        }
    }

    public func refreshStatus() async {
        do {
            let s: Status = try await live.call("status.get", Empty())
            status = s
            refreshStrip()
        } catch { lastError = String(describing: error) }
    }

    private func refreshStrip() { strip = StripModel.make(status: status, engine: engineState) }

    // MARK: network

    private func startNetworkMonitor() {
        let m = NWPathMonitor()
        m.pathUpdateHandler = { [weak self] path in
            let ok = path.status == .satisfied
            Task { @MainActor in
                self?.online = ok
                await self?.pushNetworkState()
            }
        }
        m.start(queue: DispatchQueue(label: "misty.net"))
        monitor = m
    }

    private func pushNetworkState() async {
        struct P: Encodable, Sendable { var online: Bool }
        let _: JSONValue? = try? await live.call("net.set", P(online: online))
    }

    // MARK: actions

    public func perform(_ action: Banner.Action) async {
        switch action {
        case .openFullDiskAccess:
            if let u = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") {
                NSWorkspace.shared.open(u)
            }
        case .raiseCap:
            struct P: Encodable, Sendable { var scope: String; var usd: Double }
            // during the first build the cap that ran out is the build one: raising the daily cap did nothing
            if let sp = status?.spend, sp.build != nil, status?.build != nil {
                let _: JSONValue? = try? await live.call("control.raise_cap", P(scope: "build", usd: sp.capBuild + 0.30))
            } else {
                let cap = status?.spend.capDaily ?? 0.2
                let _: JSONValue? = try? await live.call("control.raise_cap", P(scope: "daily", usd: cap * 2))
            }
        case .retryFailed:
            let _: JSONValue? = try? await live.call("jobs.retry_failed", Empty())
        case .openLogs:
            NSWorkspace.shared.open(URL(fileURLWithPath: home + "/logs"))
        case .reconnectGoogle:
            await reconnectGoogle()
        case .exportWhatsApp, .none:
            break
        }
        await refreshStatus()
    }

    /// The sign-in waits for Isaac to finish in the browser, so it gets minutes, not the usual 30 seconds.
    func reconnectGoogle() async {
        guard googleNote != "Waiting for Google…" else { return }     // already open in the browser
        googleNote = "Waiting for Google…"
        struct R: Decodable, Sendable { var email: String }
        do {
            let _: R = try await live.call("setup.google_auth_start", Empty(), timeout: .seconds(300))
            googleNote = nil
        } catch let e as RpcError {
            googleNote = Self.googleProblem(e)
        } catch { googleNote = "Google sign-in didn't finish. Try again." }
    }

    private static func googleProblem(_ e: RpcError) -> String {
        switch e {
        case .server(let code) where code == "WRONG_ACCOUNT": return "Wrong Google account. Use you@example.com."
        case .server(let code) where code == "NO_GOOGLE_CLIENT": return "Google isn't set up here yet. Run setup again."
        case .timeout: return "Google sign-in timed out. Try again."
        default: return "Google sign-in didn't finish. Try again."
        }
    }

    public func setPaused(_ paused: Bool) async {
        let _: JSONValue? = try? await live.call(paused ? "control.pause" : "control.resume", Empty())
        await refreshStatus()
    }

    public func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            launchAtLogin = on
        } catch { lastError = String(describing: error) }
    }

    public func deleteEverything() async {
        let _: JSONValue? = try? await live.call("data.delete_all", Empty())
        setupComplete = false
        try? await Task.sleep(for: .seconds(1))
        NSApp.terminate(nil)
    }
}


public enum DeleteConfirmation {
    public static let word = "DELETE"
    public static func isValid(_ typed: String) -> Bool { typed == word }
}
