import SwiftUI
import UniformTypeIdentifiers

struct SetupView: View {
    @Environment(AppState.self) private var state
    @State private var step: SetupStep = .privacy
    @State private var s = SetupState()
    @State private var apiKey = ""
    @State private var busy = false
    @State private var message: String?
    @State private var estimate: SetupEstimate?
    @State private var iMessageChats: [ChatInfoDTO] = []
    @State private var waChats: [ChatInfoDTO] = []
    @State private var excluded: Set<String> = []
    @State private var picked: Set<String> = []
    @State private var fdaTask: Task<Void, Never>?
    @State private var keySaved = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 12) {
                Orb(mood: busy ? .thinking : .idle, size: 34)
                Text("Set up EDITH").font(Theme.headline).tracking(-0.8)
                Spacer()
            }
            HStack(spacing: 6) {
                ForEach(SetupStep.allCases, id: \.rawValue) { st in
                    Capsule().fill(st.rawValue <= step.rawValue ? Theme.ember : Theme.ink.opacity(0.1))
                        .frame(width: st == step ? 28 : 8, height: 8)
                }
            }
            .help("Step \(step.rawValue + 1) of \(SetupStep.allCases.count)")
            .animation(Motion.or(Motion.morph), value: step)
            Group {
                switch step {
                case .privacy: privacy
                case .openrouter: openrouter
                case .fda: fda
                case .google: google
                case .folders: folders
                case .chats: chats
                case .build: build
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .id(step)
            .transition(.asymmetric(insertion: .move(edge: .trailing).combined(with: .opacity), removal: .opacity))
            .animation(Motion.or(Motion.morph), value: step)
            if let m = message { Label(m, systemImage: "exclamationmark.triangle.fill").foregroundStyle(Theme.warn).font(Theme.body) }
            Spacer()
            HStack {
                if step != .privacy { GoButton("Back", filled: false) { if let p = SetupStep(rawValue: step.rawValue - 1) { step = p } } }
                Spacer()
                if step == .build {
                    GoButton("Start") { Task { await startBuild() } }
                        .disabled(!SetupFlow.canAdvance(from: .build, s) || busy)
                } else {
                    GoButton("Continue") { if let n = SetupFlow.next(step) { step = n; message = nil } }
                        .disabled(!SetupFlow.canAdvance(from: step, s))
                }
            }
        }
        .padding(.horizontal, 36).padding(.top, 56).padding(.bottom, 28)
        .frame(maxWidth: 680, maxHeight: .infinity, alignment: .topLeading)
        .frame(maxWidth: .infinity)
        .task { await loadSaved() }
        .onChange(of: step) { _, new in
            fdaTask?.cancel()
            if new == .fda { startFdaPolling() }
            if new == .chats { Task { await loadChats() } }
            if new == .build { Task { await loadEstimate() } }
        }
    }

    // MARK: steps

    /// A step's head: a big icon key, the title, and one short line.
    private func head(_ icon: String, _ title: String, _ sub: String? = nil) -> some View {
        HStack(spacing: 14) {
            IconTile(icon: icon, plastic: Theme.limeSoft, side: 48)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(Theme.ui(20, 700)).tracking(-0.3)
                if let sub { Text(sub).font(Theme.body).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true) }
            }
        }
        .padding(.bottom, 6)
    }

    private func done(_ text: String) -> some View {
        Label(text, systemImage: "checkmark.circle.fill").font(Theme.ui(13, 600)).foregroundStyle(Theme.good)
    }

    private var privacy: some View {
        VStack(alignment: .leading, spacing: 14) {
            head("hand.raised.fill", "Your stuff stays yours")
            ForEach([("eye", "Reads files, mail, calendar and chats. Never writes, sends or moves."),
                     ("lock.shield", "Secrets (keys, passwords, IDs, cards) are stripped on this Mac first."),
                     ("icloud.slash", "Cloud calls use OpenRouter with Zero Data Retention only."),
                     ("trash", "Everything lives in ~/Misty. One button deletes it.")], id: \.0) { icon, text in
                HStack(spacing: 12) {
                    Image(systemName: icon).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.accent).frame(width: 22)
                    Text(text).font(Theme.ui(14, 500)).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var openrouter: some View {
        VStack(alignment: .leading, spacing: 12) {
            head("key.fill", "OpenRouter key", "$5 limit, Zero Data Retention on. Testing costs about $0.00003.")
            if keySaved { Label("Saved in Keychain", systemImage: "key.fill").font(Theme.small).foregroundStyle(Theme.dim) }
            SecureField(keySaved ? "Paste to replace" : "sk-or-v1-...", text: $apiKey)
                .textFieldStyle(.roundedBorder).font(Theme.body)
            HStack {
                GoButton(keySaved && apiKey.isEmpty ? "Test" : "Save and test", filled: false) { Task { await testOpenRouter() } }
                    .disabled((apiKey.isEmpty && !keySaved) || busy)
                if s.openrouterOk && s.zdr { done("Works, ZDR confirmed") }
            }
        }
    }

    private var fda: some View {
        VStack(alignment: .leading, spacing: 12) {
            head("internaldrive", "Full Disk Access", "To read iMessage and WhatsApp.")
            HStack {
                GoButton("Open System Settings", filled: false) { Task { await state.perform(.openFullDiskAccess) } }
                if s.fdaGranted { done("Granted") }
            }
            Toggle("Skip chats for now", isOn: $s.skipChats)
        }
    }

    private var google: some View {
        VStack(alignment: .leading, spacing: 12) {
            head("envelope.fill", "Google", "Read-only Gmail and Calendar for \(SetupFlow.allowedGoogle).")
            HStack {
                GoButton("OAuth client file…", filled: false) { chooseClient() }
                GoButton("Connect", filled: false) { Task { await connectGoogle() } }.disabled(busy)
            }
            if let e = s.googleEmail { done(e) }
            Toggle("Skip Google for now", isOn: $s.googleSkipped)
        }
    }

    private var folders: some View {
        VStack(alignment: .leading, spacing: 12) {
            head("folder.fill", "Folders", "Read only. Never ~/Library or ~/Misty.")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 8)], alignment: .leading, spacing: 8) {
                ForEach(["~/Desktop", "~/Downloads", "~/Notes", "~/Code", "~/Projects", "~/Pictures"], id: \.self) { f in
                    Label(f, systemImage: "folder.fill").font(Theme.ui(13, 550))
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.raised))
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.line))
                }
            }
        }
    }

    private var chats: some View {
        VStack(alignment: .leading, spacing: 10) {
            if s.skipChats {
                head("bubble.left.and.bubble.right.fill", "Chats skipped", "Add them later in Settings.")
            } else {
                Label("WhatsApp: pick chats", systemImage: "phone.bubble.fill").font(Theme.title)
                List(waChats) { c in
                    Toggle(isOn: Binding(get: { picked.contains(c.id) },
                                         set: { if $0 { picked.insert(c.id) } else { picked.remove(c.id) } })) {
                        VStack(alignment: .leading) { Text(c.name); Text(c.lastMessageAt ?? "").font(Theme.small).foregroundStyle(Theme.dim) }
                    }
                }.frame(height: 160)
                Label("iMessage: untick to skip", systemImage: "message.fill").font(Theme.title)
                List(iMessageChats) { c in
                    Toggle(isOn: Binding(get: { !excluded.contains(c.id) },
                                         set: { if $0 { excluded.remove(c.id) } else { excluded.insert(c.id) } })) { Text(c.name) }
                }.frame(height: 160)
            }
        }
    }

    private var build: some View {
        VStack(alignment: .leading, spacing: 10) {
            head("hammer.fill", "First build")
            if let e = estimate {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(String(format: "$%.2f", e.totalUsd)).font(Theme.ui(32, 700)).monospacedDigit()
                    Text(String(format: "of $%.2f · %d items", e.capUsd, e.items)).font(Theme.body).foregroundStyle(Theme.dim)
                }
                ForEach(e.bySource.sorted(by: { $0.key < $1.key }), id: \.key) { k, v in
                    Text("\(k): \(v.items) items").font(Theme.small).foregroundStyle(Theme.dim)
                }
                if let over = SetupFlow.overCapMessage(s) { Text(over).foregroundStyle(Theme.urgent) }
            } else {
                HStack(spacing: 10) { Orb(mood: .thinking, size: 20); Text("Estimating").foregroundStyle(Theme.dim) }
            }
            if let b = state.status?.build { ProgressView(value: Double(b.done), total: Double(max(b.total, 1))) }
        }
    }

    // MARK: actions

    private func testOpenRouter() async {
        busy = true; defer { busy = false }
        struct K: Encodable, Sendable { var key: String }
        do {
            if !apiKey.isEmpty {                     // empty means: test the key already in the Keychain
                let _: JSONValue = try await state.live.call("setup.set_openrouter_key", K(key: apiKey))
                apiKey = ""
                keySaved = true
            }
            let r: OpenRouterTest = try await state.live.call("setup.test_openrouter", Empty())
            s.openrouterOk = r.ok
            s.zdr = r.zdr
            message = r.ok ? nil : "The test call did not work."
        } catch { message = "Test failed: \(error)" }
    }

    /// Picks up what the CLI or an earlier run already stored (local Keychain check, no network).
    private func loadSaved() async {
        for _ in 0..<120 {                           // the engine may still be starting (imports take a while)
            if let r: SavedState = try? await state.live.call("setup.saved_state", Empty()) {
                keySaved = r.openrouterKey
                if let e = r.googleEmail, s.googleEmail == nil { s.googleEmail = e }
                return
            }
            try? await Task.sleep(for: .milliseconds(500))
        }
    }

    private func startFdaPolling() {
        fdaTask = Task {
            let ok = await SetupFlow.pollUntilGranted(check: {
                struct R: Decodable, Sendable { var granted: Bool }
                let r: R? = try? await state.live.call("setup.check_fda", Empty())
                return r?.granted ?? false
            })
            if ok { s.fdaGranted = true }
        }
    }

    private func chooseClient() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let url = panel.url, let text = try? String(contentsOf: url) else { return }
        struct P: Encodable, Sendable { var json: String }
        Task {
            do { let _: JSONValue = try await state.live.call("setup.google_import_client", P(json: text)); message = nil }
            catch { message = "That is not a Google OAuth client file." }
        }
    }

    private func connectGoogle() async {
        busy = true; defer { busy = false }
        struct R: Decodable, Sendable { var email: String }
        do {
            let r: R = try await state.live.call("setup.google_auth_start", Empty())
            s.googleEmail = r.email
            message = nil
        } catch { message = "Google sign-in failed or used the wrong account." }
    }

    private func loadChats() async {
        guard !s.skipChats else { return }
        if let l: ChatList = try? await state.live.call("setup.list_whatsapp_chats", Empty()) { waChats = l.chats }
        if let l: ChatList = try? await state.live.call("setup.list_imessage_chats", Empty()) { iMessageChats = l.chats }
    }

    private func loadEstimate() async {
        struct P: Encodable, Sendable { var enabled: [String: Bool]; var imessageExcluded: [String]; var whatsappPicked: [String] }
        let _: JSONValue? = try? await state.live.call("setup.save_sources", P(
            enabled: ["gmail": s.googleEmail != nil, "gcal": s.googleEmail != nil,
                      "imessage": !s.skipChats, "whatsapp": !s.skipChats && !picked.isEmpty],
            imessageExcluded: Array(excluded), whatsappPicked: Array(picked)))
        if s.googleEmail != nil { let _: JSONValue? = try? await state.live.call("setup.measure_gmail", Empty()) }
        if let e: SetupEstimate = try? await state.live.call("setup.estimate", Empty()) {
            estimate = e
            s.estimateTotal = e.totalUsd
            s.capUsd = e.capUsd
            s.estimateShown = true
        }
    }

    private func startBuild() async {
        busy = true; defer { busy = false }
        struct P: Encodable, Sendable { var limit: Int?; var userApproved: Bool }
        do {   // this click, with the estimate and cap on screen, is the yes to paid calls (caps still apply)
            let _: JSONValue = try await state.live.call("setup.start_build", P(limit: nil, userApproved: true))
            state.setupComplete = true
        } catch { message = "Could not start the build: \(error)" }
    }
}
