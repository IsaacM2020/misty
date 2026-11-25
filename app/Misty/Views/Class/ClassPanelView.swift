import SwiftUI

/// Inside the class panel. Before class: pick a subject and start. During class: Notes (his typing) and Transcript
/// tabs with an Ask box. After: the notes for that class as they're made.
struct ClassPanelView: View {
    @Environment(AppState.self) private var state
    @State private var tab = "Notes"

    var body: some View {
        let vm = state.classes
        VStack(spacing: 0) {
            if vm.isOpen || vm.state == .warming {
                LiveHeader()
                Picker("", selection: $tab) { Text("Notes").tag("Notes"); Text("Transcript").tag("Transcript") }
                    .pickerStyle(.segmented).labelsHidden().padding(.horizontal, 14).padding(.bottom, 8).tint(Theme.lime)
                if tab == "Notes" { NotesEditor() } else { TranscriptList() }
                AskBox(session: vm.session?.id)
            } else if let d = vm.detail {
                ClassNotesView(detail: d).padding(.top, 24)
                HStack {
                    Spacer()
                    GoButton("New class", filled: false) { vm.closeDetail() }
                }
                .padding(12)
            } else {
                StartClass()
            }
        }
        .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }
}

private struct StartClass: View {
    @Environment(AppState.self) private var state
    var body: some View {
        @Bindable var vm = state.classes
        VStack(alignment: .leading, spacing: 14) {
            Text("Take notes in class").font(Theme.ui(26, 760, width: 92)).tracking(-0.5)
            Text("EDITH turns the class into text on this Mac (no audio is kept) and makes full notes when you end it.")
                .font(Theme.body).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
            Picker("Subject", selection: Binding(get: { vm.subject }, set: { vm.pick($0) })) { ForEach(ClassVM.subjects, id: \.self) { Text($0) } }
                .frame(maxWidth: 220)
            VStack(alignment: .leading, spacing: 6) {
                Picker("", selection: $vm.mode) {
                    Text("Online").tag(ClassVM.Mode.online)
                    Text("In person").tag(ClassVM.Mode.room)
                }
                .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 240).tint(Theme.lime)
                Text(vm.mode == .online
                     ? "Zoom, Meet, Teams, anything: EDITH listens to the Mac's sound as the teacher and the mic as you."
                     : "One mic for the room. Sit where you can hear the teacher.")
                    .font(Theme.small).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
                if let h = vm.hint { Text(h).font(Theme.small).foregroundStyle(Theme.accent) }
            }
            if let p = vm.problem { Text(p).font(Theme.small).foregroundStyle(Theme.urgent) }
            if let e = vm.endedNote { Text(e).font(Theme.small).foregroundStyle(Theme.dim) }
            GoButton("Start class") { Task { await vm.start() } }
            Spacer()
        }
        .padding(.horizontal, 18).padding(.top, 36)
        .frame(maxWidth: .infinity, alignment: .leading)
        .task { await vm.suggest() }
    }
}

private struct LiveHeader: View {
    @Environment(AppState.self) private var state
    var body: some View {
        let vm = state.classes
        HStack(spacing: 8) {
            Circle().fill(vm.state == .live ? Theme.urgent : Theme.faint).frame(width: 8, height: 8)
                .help("Transcribing: EDITH is turning what she hears into text")
            Text(vm.session?.title ?? vm.subject).font(Theme.ui(15, 700)).lineLimit(1)
            Text(ClassTime.mmss(vm.elapsed)).font(Theme.mono).foregroundStyle(Theme.dim)
            Spacer()
            switch vm.state {
            case .warming: Text("Ears warming up").font(Theme.small).foregroundStyle(Theme.dim)
            case .paused(let why):
                Text(why == "lid" ? "Paused (lid)" : "Paused").font(Theme.small).foregroundStyle(Theme.dim)
                QuietButton("Resume") { Task { await vm.resume() } }
            case .live: QuietButton("Pause") { Task { await vm.pause() } }
            default: EmptyView()
            }
            GoButton("End") { Task { await vm.end() } }
        }
        .padding(.horizontal, 14).padding(.top, 30).padding(.bottom, 4)
        if vm.mode == .online, vm.slideCount > 0 {
            Text("Read \(vm.slideCount) slide\(vm.slideCount == 1 ? "" : "s") from the shared screen (text only, nothing saved as a picture)")
                .font(Theme.small).foregroundStyle(Theme.dim)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 14).padding(.bottom, 6)
        }
        if let p = vm.macProblem {
            Text(p).font(Theme.small).foregroundStyle(Theme.warn)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 14).padding(.bottom, 6)
        }
    }
}

private struct NotesEditor: View {
    @Environment(AppState.self) private var state
    var body: some View {
        let vm = state.classes
        TextEditor(text: Binding(get: { vm.notesText }, set: { vm.notesChanged(vm.stamped($0)) }))
            .font(Theme.body)
            .scrollContentBackground(.hidden)
            .padding(.horizontal, 10)
            .overlay(alignment: .topLeading) {
                if vm.notesText.isEmpty {
                    Text("Type notes if you want. EDITH writes full notes either way.")
                        .font(Theme.body).foregroundStyle(Theme.faint).padding(.horizontal, 15).padding(.top, 1)
                        .allowsHitTesting(false)
                }
            }
    }
}

private struct TranscriptList: View {
    @Environment(AppState.self) private var state
    @State private var follow = true

    var body: some View {
        let vm = state.classes
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    if vm.segments.isEmpty && vm.partialLine.isEmpty {
                        Text("Nothing heard yet.").font(Theme.small).foregroundStyle(Theme.faint)
                    }
                    ForEach(vm.segments) { s in
                        TranscriptRow(t: s.tStart, text: s.text, who: ClassWho.label(source: s.source, online: vm.mode == .online)).id(s.id)
                    }
                    if !vm.partialLine.isEmpty {
                        let who = ClassWho.label(source: vm.partialSource, online: vm.mode == .online)
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            if let who { WhoChip(who: who).opacity(0.6) }
                            Text(vm.partialLine).font(Theme.body).foregroundStyle(Theme.faint)
                        }.id(-1)
                    }
                    Color.clear.frame(height: 1).id(-2)
                        .onAppear { follow = true }
                        .onDisappear { follow = false }
                }
                .padding(.horizontal, 14).padding(.vertical, 6)
            }
            .onChange(of: vm.segments.count) { if follow { proxy.scrollTo(-2, anchor: .bottom) } }
            .onChange(of: vm.partialLine) { if follow { proxy.scrollTo(-2, anchor: .bottom) } }
            .overlay(alignment: .bottom) {
                if !follow {
                    QuietButton("Jump to now") { proxy.scrollTo(-2, anchor: .bottom) }
                        .background(Theme.raised, in: Capsule()).padding(.bottom, 6)
                }
            }
        }
    }
}

struct TranscriptRow: View {
    let t: Double
    let text: String
    var who: String? = nil              // "Teacher" / "Me" in an online class
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(ClassTime.mmss(t)).font(Theme.mono).foregroundStyle(Theme.faint)
            if let who { WhoChip(who: who) }
            Text(text).font(Theme.body).textSelection(.enabled)
        }
    }
}

/// Who spoke in an online class: the teacher on a highlighter chip, Isaac on an outlined one, so the two read apart at a glance.
struct WhoChip: View {
    let who: String
    var body: some View {
        let teacher = who == "Teacher"
        Text(who).font(Theme.small).fontWeight(.bold).foregroundStyle(Theme.ink)
            .frame(width: 56)
            .padding(.vertical, 2)
            .background(teacher ? Theme.lime : Color.clear, in: Capsule())
            .overlay(Capsule().stroke(Theme.ink.opacity(teacher ? 0 : 0.35), lineWidth: 1))
    }
}

/// Ask about this class (or, with no class, across classes). Answers come with the times they came up.
struct AskBox: View {
    @Environment(AppState.self) private var state
    let session: String?
    var subject: String? = nil
    @State private var q = ""
    @State private var answer: ClassAnswer?
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let a = answer {
                VStack(alignment: .leading, spacing: 6) {
                    Text(a.answer).font(Theme.body).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    if !a.cites.isEmpty {
                        Text(a.cites.map { ClassTime.mmss($0.tStart) }.joined(separator: "  "))
                            .font(Theme.mono).foregroundStyle(Theme.accent)
                    }
                }
                .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.raised, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
            HStack(spacing: 8) {
                Image(systemName: "sparkle").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.accent)
                TextField(session == nil ? "Ask your classes" : "Ask this class", text: $q)
                    .textFieldStyle(.plain).font(Theme.body)
                    .onSubmit(go)
                if busy { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(Theme.panel, in: Capsule(style: .continuous))
        }
        .padding(12)
    }

    private func go() {
        let text = q.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty, !busy else { return }
        busy = true
        Task {
            answer = await state.classes.ask(text, session: session, subject: subject)
            busy = false
        }
    }
}
