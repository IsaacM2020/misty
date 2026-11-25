import AppKit
import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var state
    @State private var typed = ""
    @State private var confirming = false
    @State private var dailyCap = 0.20
    @State private var buildCap = 3.0
    @State private var choosingChats = false
    @State private var showingCosts = false
    @AppStorage("uiSounds") private var uiSounds = true

    private static let sources: [(key: String, name: String, icon: String)] = [
        ("folders", "Folders", "folder"), ("gmail", "Gmail", "envelope"), ("gcal", "Google Calendar", "calendar"),
        ("imessage", "iMessage", "message"), ("whatsapp", "WhatsApp", "bubble.left.and.bubble.right"),
        ("claude_mem", "Claude memory", "brain"),
    ]

    @State private var folderNote: String?

    /// What tapping a source tile does. Claude memory has nothing to set, so it is not a button.
    private func tapAction(_ key: String) -> (() -> Void)? {
        switch key {
        case "gmail", "gcal": return { Task { await state.reconnectGoogle() } }
        case "imessage", "whatsapp": return { choosingChats = true }
        case "folders": return { addFolders() }
        default: return nil
        }
    }

    static func hint(_ key: String) -> String {
        switch key {
        case "gmail", "gcal": return "Sign in to Google again (also allows adding events and mail drafts)"
        case "imessage", "whatsapp": return "Choose which chats EDITH reads"
        case "folders": return "Add a folder for EDITH to read"
        default: return "Read automatically, nothing to set"
        }
    }

    private func addFolders() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = true
        panel.prompt = "Add"
        panel.message = "Pick folders for EDITH to read. She never edits them."
        guard panel.runModal() == .OK, !panel.urls.isEmpty else { return }
        struct P: Encodable, Sendable { var paths: [String] }
        struct R: Decodable, Sendable { var folders: [String] }
        let paths = panel.urls.map(\.path)
        Task {
            do {
                let r: R = try await state.live.call("settings.add_folders", P(paths: paths))
                folderNote = "Reading \(r.folders.count) folder\(r.folders.count == 1 ? "" : "s") now."
            } catch { folderNote = "Couldn't add that folder. Try again." }
        }
    }

    private func set(_ key: String, _ value: JSONValue) {
        struct P: Encodable, Sendable { var key: String; var value: JSONValue }
        Task { let _: JSONValue? = try? await state.live.call("settings.set", P(key: key, value: value)) }
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Settings")
            form
        }
    }

    private var form: some View {
        Form {
            PermissionsSection()
            VoiceSection()
            if DevVoices.enabled { DevVoiceSection() }
            DictationSection()
            Section {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 10)], spacing: 10) {
                    ForEach(Self.sources, id: \.key) { src in
                        SourceTile(name: src.name, icon: src.icon,
                                   health: state.status?.sources.first { $0.source == src.key }?.health,
                                   hint: Self.hint(src.key), tap: tapAction(src.key))
                    }
                }
                .padding(.vertical, 4)
                if let note = state.googleNote {
                    Text(note).font(Theme.small).foregroundStyle(note.hasPrefix("Waiting") ? Theme.dim : Theme.warn)
                }
                if let folderNote { Text(folderNote).font(Theme.small).foregroundStyle(Theme.dim) }
                Button("Choose chats…") { choosingChats = true }
                    .help("Which WhatsApp chats EDITH reads and which iMessage chats it skips")
            } header: { Label("Sources", systemImage: "tray.2") }
            Section {
                Stepper(value: $dailyCap, in: 0.05...5, step: 0.05) {
                    LabeledContent("Daily", value: String(format: "$%.2f", dailyCap))
                }
                .help("Resets at midnight, Singapore time")
                .onChange(of: dailyCap) { _, v in set("cap_daily_usd", .number(v)) }
                Stepper(value: $buildCap, in: 1...20, step: 0.5) {
                    LabeledContent("First build", value: String(format: "$%.2f", buildCap))
                }
                .onChange(of: buildCap) { _, v in set("cap_build_usd", .number(v)) }
                Button("Where the money goes…") { showingCosts = true }
            } header: { Label("Spending", systemImage: "dollarsign.circle") }
            Section {
                Toggle("Pause everything", isOn: Binding(get: { state.status?.paused ?? false },
                                                         set: { v in Task { await state.setPaused(v) } }))
                Toggle("Launch at login", isOn: Binding(get: { state.launchAtLogin }, set: { state.setLaunchAtLogin($0) }))
                Toggle("Button sounds", isOn: $uiSounds)
            } header: { Label("EDITH", systemImage: "drop.fill") }
            Section {
                Button("Delete all my data", role: .destructive) { confirming = true }
            } header: { Label("Data", systemImage: "externaldrive") }
        }
        .formStyle(.grouped)
        // On the form, not on PermissionsSection: that section is empty until the first refresh, and a task on an
        // empty view never runs, so the list never loaded and the section never showed.
        .task {
            while !Task.isCancelled {
                await state.permissions.refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
        .scrollContentBackground(.hidden)
        .tint(Theme.ember)
        .frame(maxWidth: 760)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Page.gutter - 20)
        .onAppear {
            if let s = state.status { dailyCap = s.spend.capDaily; buildCap = s.spend.capBuild }
        }
        .sheet(isPresented: $choosingChats) { ChatPickerView() }
        .sheet(isPresented: $showingCosts) { CostsView() }
        .sheet(isPresented: $confirming) {
            VStack(alignment: .leading, spacing: 14) {
                Text("Delete everything EDITH stored?").font(Theme.title)
                Text("This removes ~/Misty (index, wiki, logs, models) and every EDITH key in the Keychain. Your own files, mail and chats are untouched. Type DELETE to confirm.")
                    .fixedSize(horizontal: false, vertical: true)
                TextField("DELETE", text: $typed).textFieldStyle(.roundedBorder)
                HStack {
                    Button("Cancel") { confirming = false; typed = "" }
                    Spacer()
                    Button("Delete", role: .destructive) { Task { await state.deleteEverything() } }
                        .disabled(!DeleteConfirmation.isValid(typed))
                }
            }
            .padding(20)
            .frame(width: 420)
        }
    }
}

/// A source as a small tile: its icon, its name, and a dot that is green when it works.
private struct SourceTile: View {
    let name: String
    let icon: String
    let health: String?
    let hint: String
    let tap: (() -> Void)?

    private var color: Color {
        switch health {
        case "ok": return Theme.good
        case nil: return Theme.faint
        default: return Theme.warn
        }
    }

    var body: some View {
        if let tap {
            Button(action: tap) { tile }.buttonStyle(.plain).help(hint)
        } else {
            tile.help(hint)
        }
    }

    private var tile: some View {
        VStack(spacing: 7) {
            Image(systemName: icon).font(.system(size: 16, weight: .semibold))
                .foregroundStyle(health == "ok" ? Theme.text : Theme.faint)
                .frame(width: 38, height: 38)
                .background(Circle().fill(Theme.panel))
                .overlay(alignment: .bottomTrailing) {
                    Circle().fill(color).frame(width: 10, height: 10).overlay(Circle().strokeBorder(Theme.raised, lineWidth: 2))
                }
            Text(name).font(Theme.ui(11.5, 550)).foregroundStyle(Theme.dim).lineLimit(1).minimumScaleFactor(0.85)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }
}

/// Every macOS permission in one list. Granted ones sit quiet; missing ones get one button. Re-checks every 2 s while
/// open, so flipping a switch in System Settings shows up here without a restart.
private struct VoiceSection: View {
    @Environment(AppState.self) private var state
    @State private var picked = ""
    @State private var rate: Double = Double(Speaker.defaultRate)

    var body: some View {
        @Bindable var voice = state.voice
        Section {
            LabeledContent("Talk") { KeyHint(keys: ["⌘", "⌥"], side: "Left") }
            Toggle("Class mode (silent)", isOn: $voice.classMode)
            Picker("Always listening", selection: Binding(get: { VoiceVM.ambientWanted == .off ? voice.ambient : VoiceVM.ambientWanted },
                                                          set: { m in Task { await voice.setAmbient(m) } })) {
                Text("Off").tag(AmbientMode.off)
                Text("Her name only").tag(AmbientMode.name)
                Text("Just talk").tag(AmbientMode.judge)
            }
            Text("The mic stays open. Her name only: she answers to Edith or Misty (first or last word) or right after her own answer. Just talk: also anything a small model judges was said to her, using what app is in front and whether the room is talking; a wrong guess stays quiet. Words not for her are dropped and never written down. Pauses in a class, a call, while the Mac sleeps and for 2 minutes after the talk key, then comes back by itself.")
                .font(.caption).foregroundStyle(.secondary)
            Toggle("Say \u{201C}Edith\u{201D} to talk", isOn: Binding(get: { state.wake.settings.enabled }, set: { state.setWakeEnabled($0) }))
            Text(wakeCaption(compiled: WakeModel.compiledIn, installed: WakeModel.installed(home: state.home), listening: state.wake.listening))
                .font(.caption).foregroundStyle(.secondary)
            Toggle("Speak replies", isOn: $voice.speakReplies)
            Toggle("Experimental: LFM3 voice (local)", isOn: Binding(
                get: { voice.lfm.state == .starting || voice.lfm.state == .ready },
                set: { on in if on { voice.lfm.enable(classMode: voice.inClass) } else { voice.lfm.disable() } }))
                .disabled(voice.inClass)
            Text(lfmCaption(voice.lfm.state))
                .font(.caption).foregroundStyle(voice.lfm.state.isFailure ? Color.red : Color.secondary)
            if !state.voice.voices.isEmpty {
                Picker("Voice", selection: Binding(get: { picked }, set: { v in
                    picked = v
                    Task { await state.voice.setVoice(v) }                     // changing it is silent; the button plays it
                })) {
                    ForEach(state.voice.voices.filter { !$0.hasPrefix("System: ") }, id: \.self) { Text($0).tag($0) }
                }
            }
            LabeledContent("Speed") {
                HStack {
                    Slider(value: Binding(get: { rate }, set: { r in rate = r; state.voice.setSpeechRate(Float(r)) }),
                           in: Double(Speaker.rateRange.lowerBound)...Double(Speaker.rateRange.upperBound), step: 0.05)
                        .frame(width: 160)
                    Text(String(format: "%.2fx", rate)).font(Theme.small).foregroundStyle(Theme.dim).monospacedDigit()
                }
            }
            Button { state.voice.speakSample() } label: { Label("Play sample", systemImage: "play.fill") }
                .disabled(state.voice.inClass)
        } header: {
            Label("Voice", systemImage: "waveform")
                .help("Tap, talk, tap again to send. Tap while she talks to cut her off. Left Control+Option works too.")
        }
        .onAppear { picked = state.voice.voiceName; rate = Double(state.voice.speechRate) }
    }
}

private func wakeCaption(compiled: Bool, installed: Bool, listening: Bool) -> String {
    if !compiled { return "This build has no wake word library. Run scripts/fetch_wake_model.sh, then rebuild." }
    if !installed { return "The wake word model is not on this Mac yet. Run scripts/fetch_wake_model.sh." }
    return (listening ? "Listening for her name now. " : "")
        + "Same as tapping the talk key, hands free: she chimes, you talk, and she sends when you go quiet. Runs on this Mac only; no words are kept or sent. Never wakes while she talks, in a class, in a call, while dictating or in always-listening mode."
}

private func lfmCaption(_ s: LFMState) -> String {
    switch s {
    case .off: return "Talk to Liquid's LFM3-Omni-2B on this Mac, speech in and speech out, with its own voice. It has no EDITH memory or tools, so it is a feel test only. First time it downloads about 1.4 GB. Off at every launch, never in class."
    case .starting: return "Starting the local model (the first time it downloads about 1.4 GB). Push to talk stays normal EDITH until it says On."
    case .ready: return "On. Push to talk goes to LFM3 and it answers in its own voice. About 3 GB of memory while on. Turn this off to give it back."
    case .failed(let why): return why
    }
}

/// Ripple 2's push-to-talk dictation, now inside Misty. Off until it is switched on here.
private struct DictationSection: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let d = state.dictation
        Section {
            Toggle("Hold to dictate", isOn: Binding(get: { d.enabled }, set: { d.setEnabled($0) }))
            if d.enabled {
                LabeledContent("Hold") { KeyHint(keys: ["⌥"], side: "Right") }
                LabeledContent("Hands-free") { KeyHint(keys: ["/"], side: "+ hold") }
                if !d.keyOK {
                    Label("Needs Accessibility", systemImage: "exclamationmark.triangle.fill")
                        .font(Theme.small).foregroundStyle(Theme.urgent)
                        .help("Allow EDITH under Accessibility, then switch this off and on.")
                }
                TimelineView(.periodic(from: .now, by: 2)) { _ in
                    if d.rippleRunning {
                        HStack {
                            Label("Ripple 2 is running too", systemImage: "exclamationmark.triangle.fill")
                                .font(Theme.small).foregroundStyle(Theme.warn)
                                .help("Both would type your words.")
                            Spacer()
                            GoButton("Quit Ripple 2", filled: false) { d.quitRipple() }
                        }
                    }
                }
            }
        } header: {
            Label("Dictation", systemImage: "mic")
                .help("Hold, talk, let go: the words land at your cursor. Runs on this Mac only.")
        }
    }
}

private struct PermissionsSection: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let vm = state.permissions
        Group { if !vm.items.isEmpty { Section {
            ForEach(vm.items) { PermRow(item: $0) }
        } header: {
            HStack {
                Label("Permissions", systemImage: "lock.shield")
                Spacer()
                if vm.missing > 0 {
                    GoButton(vm.working == nil ? "Set up all" : "Asking…") { Task { await vm.setUpAll() } }
                } else if !vm.items.isEmpty {
                    Label("All set", systemImage: "checkmark.seal.fill").font(Theme.small).foregroundStyle(Theme.good)
                }
            }
        } } }
    }
}

private struct PermRow: View {
    @Environment(AppState.self) private var state
    let item: PermItem

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: Self.icon(item.kind)).font(.system(size: 12, weight: .semibold))
                .foregroundStyle(item.state == .granted ? Theme.good : Theme.dim).frame(width: 20)
            Text(item.name).help(item.why)
            Spacer()
            switch item.state {
            case .granted:
                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.good).help("Allowed")
            case .notYet:
                QuietButton(state.permissions.working == item.id ? "Asking…" : "Ask now") {
                    Task { await state.permissions.ask(item) }
                }
            case .denied:
                Image(systemName: "xmark.octagon.fill").foregroundStyle(Theme.warn).help("Blocked")
                QuietButton("Open Settings") { state.permissions.open(item.kind) }
            }
        }
    }

    static func icon(_ k: PermItem.Kind) -> String {
        switch k {
        case .accessibility: return "accessibility"
        case .screen: return "rectangle.dashed.badge.record"
        case .input: return "keyboard"
        case .fullDisk: return "internaldrive"
        case .microphone: return "mic.fill"
        case .automation: return "gearshape.2"
        }
    }
}

/// Keys drawn as little caps, for shortcuts.
private struct KeyHint: View {
    let keys: [String]
    var side: String? = nil
    var body: some View {
        HStack(spacing: 4) {
            if let side { Text(side).font(Theme.ui(11.5, 500)).foregroundStyle(Theme.faint) }
            ForEach(keys, id: \.self) { k in
                Text(k).font(Theme.ui(12, 600)).foregroundStyle(Theme.text)
                    .frame(minWidth: 22, minHeight: 22).padding(.horizontal, 3)
                    .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(Theme.raised)
                        .shadow(color: Theme.shadow, radius: 1, y: 1))
                    .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(Theme.line))
            }
        }
    }
}

/// Dev only (Isaac's own testing; shown only when the misty.devTools flag is on). Same voice setting as the Voice section, so the pick
/// speaks Misty's real replies at once and is kept across relaunches. The system voices are listed right away; Phonon's voices
/// join the list when its model has loaded.
private struct DevVoiceSection: View {
    @Environment(AppState.self) private var state
    @State private var ears = EarsModel.current

    var body: some View {
        let current = state.voice.voiceName
        let phonon = state.voice.voices.filter { !DevVoices.isSystem($0) }
        let system = DevVoices.systemVoices()
        Section {
            Picker("Voice engine", selection: Binding(get: { current }, set: { v in Task { await state.voice.setVoice(v) } })) {
                if !phonon.contains(current) && !system.contains(current) { Text(current).tag(current) }   // the pick, even before it is listed
                ForEach(phonon, id: \.self) { Text("Phonon: \($0)").tag($0) }
                ForEach(system, id: \.self) { Text($0).tag($0) }
            }
            if phonon.isEmpty {
                Text("Phonon's own voices appear when its model has loaded.").font(Theme.small).foregroundStyle(Theme.dim)
            }
            Button { state.voice.speakSample() } label: { Label("Play sample", systemImage: "play.fill") }
                .disabled(state.voice.inClass)
            Picker("Ears model", selection: Binding(get: { ears }, set: { m in ears = m; EarsModel.current = m })) {
                ForEach(EarsModel.available(), id: \.self) { Text($0.label).tag($0) }
            }
            if ears != .phonon2 {
                Text("This model reads each phrase after you stop talking (and dictation). The live words while you talk still come from Phonon-2.")
                    .font(Theme.small).foregroundStyle(Theme.dim)
            }
        } header: {
            Label("Dev", systemImage: "hammer")
        }
    }
}
