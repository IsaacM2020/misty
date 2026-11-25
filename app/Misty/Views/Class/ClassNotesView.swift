import SwiftUI

/// One class's notes: summary, sections (his lines in full text, Misty's dimmer), Remember, action items, and what
/// was missed. Every point has its time; tapping one shows the transcript at that moment.
struct ClassNotesView: View {
    @Environment(AppState.self) private var state
    let detail: ClassDetail
    @State private var showTranscript = false
    @State private var at: Int?
    @State private var gist = false
    @State private var renaming = false
    @State private var draftTitle = ""
    @State private var draftSubject = ""

    var body: some View {
        let s = detail.session
        let status = state.classes.noteStatus[s.id] ?? s.status
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {       // lazy: a class has 500 to 1,000 transcript rows
                    HStack(alignment: .center, spacing: 12) {
                        IconTile(icon: SubjectStyle.icon(s.subject), plastic: SubjectStyle.plastic(s.subject), side: 40)
                        VStack(alignment: .leading, spacing: 2) {
                            if renaming {
                                HStack(spacing: 8) {
                                    TextField("Class name", text: $draftTitle).textFieldStyle(.roundedBorder).frame(maxWidth: 280)
                                        .onSubmit { saveName(s) }
                                    Picker("", selection: $draftSubject) { ForEach(ClassVM.subjects, id: \.self) { Text($0) } }
                                        .labelsHidden().frame(width: 110)
                                    GoButton("Save") { saveName(s) }
                                    QuietButton("Cancel") { renaming = false }
                                }
                            } else {
                                HStack(spacing: 6) {
                                    Text(s.title).font(Theme.ui(21, 700)).tracking(-0.4)
                                    Button { draftTitle = s.title; draftSubject = s.subject; renaming = true } label: {
                                        Image(systemName: "pencil").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.faint)
                                    }
                                    .buttonStyle(.plain).help("Rename this class or change its subject")
                                }
                            }
                            HStack(spacing: 6) {
                                if s.isOnline { Label("Online", systemImage: "video.fill") }
                                else { Label("In room", systemImage: "person.2.fill") }
                            }
                            .font(Theme.ui(11.5, 600)).foregroundStyle(Theme.faint)
                        }
                        .help("Notes cost " + ClassCost.label(s.cost ?? 0))
                        Spacer()
                        Segmented(options: [(false, "Notes"), (true, "Transcript")], selection: $showTranscript)
                    }
                    .padding(.bottom, 6)
                    if showTranscript {
                        if detail.segments.isEmpty {
                            Text(s.pruned == 1 ? "The transcript is gone (kept 6 months)." : "No transcript.")
                                .font(Theme.small).foregroundStyle(Theme.dim)
                        }
                        ForEach(detail.segments) { g in
                            TranscriptRow(t: g.tStart, text: g.text, who: ClassWho.label(source: g.source, online: s.isOnline)).id(g.id)
                                .background(at == g.id ? Theme.emberSoft : .clear)
                        }
                    } else if let n = s.notes, status == "noted" {
                        notes(n)
                    } else {
                        statusLine(status, s.id)
                    }
                }
                .padding(.horizontal, 18).padding(.bottom, 16)
            }
            .onChange(of: at) { if let a = at { withAnimation { proxy.scrollTo(a, anchor: .center) } } }
        }
    }

    private func saveName(_ s: ClassSession) {
        let t = draftTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        renaming = false
        guard !t.isEmpty, t != s.title || draftSubject != s.subject else { return }
        Task { await state.classes.rename(s.id, title: t == s.title ? nil : t, subject: draftSubject == s.subject ? nil : draftSubject) }
    }

    @ViewBuilder private func statusLine(_ status: String, _ id: String) -> some View {
        switch status {
        case "too_little": Label("Too little heard", systemImage: "ear.trianglebadge.exclamationmark").font(Theme.body).foregroundStyle(Theme.dim)
        case "failed":
            HStack { Label("No notes yet", systemImage: "exclamationmark.circle").font(Theme.body).foregroundStyle(Theme.warn)
                     GoButton("Retry", filled: false) { Task { await state.classes.regenerate(id) } } }
        default:
            HStack(spacing: 10) { Orb(mood: .thinking, size: 22); Text("Writing notes").font(Theme.body).foregroundStyle(Theme.dim) }
        }
    }

    @ViewBuilder private func notes(_ n: ClassNotes) -> some View {
        // The gist, in her colour.
        HStack(alignment: .top, spacing: 10) {
            Orb(size: 18).padding(.top, 1)
            Text(n.summary).font(Theme.ui(14, 500)).lineSpacing(3).lineLimit(gist ? nil : 4)
                .fixedSize(horizontal: false, vertical: true)
        }
        .contentShape(Rectangle())
        .onTapGesture { withAnimation(Motion.or(Motion.morph)) { gist.toggle() } }
        .help(gist ? "" : "Tap to read it all")
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.emberSoft))
        ForEach(n.gaps.indices, id: \.self) { i in
            let g = n.gaps[i]
            Label("\(ClassTime.mmss(g.from))–\(ClassTime.mmss(g.to ?? g.from)) missed", systemImage: g.reason == "lid" ? "laptopcomputer.slash" : "ear.trianglebadge.exclamationmark")
                .font(Theme.ui(12, 600)).foregroundStyle(Theme.urgent)
                .help(g.reason == "lid" ? "Lid closed" : g.reason)
        }
        if !detail.actions.isEmpty {
            block("To do", icon: "checklist", tint: Theme.accent) {
                ForEach(detail.actions) { a in
                    let done = a.done == 1
                    Button { if !done { Sound.shared.play(.done) }; Task { await state.classes.setActionDone(a.id, !done) } } label: {
                        HStack(alignment: .center, spacing: 10) {
                            Image(systemName: done ? "checkmark.circle.fill" : "circle").font(.system(size: 17))
                                .foregroundStyle(done ? Theme.ember : Theme.faint).contentTransition(.symbolEffect(.replace))
                            Text(a.text).font(Theme.body).foregroundStyle(done ? Theme.faint : Theme.text).strikethrough(done)
                                .multilineTextAlignment(.leading)
                            Spacer(minLength: 8)
                            let dl = a.due.map { Pretty.day($0) } ?? a.dueText
                            if !dl.isEmpty, !dl.lowercased().contains("not given"), dl.count < 18 { Chip(dl) }
                            chip(a.segId)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        if let lost = n.lost, !lost.isEmpty {
            block("Where you got lost", icon: "questionmark.circle.fill", tint: Theme.warn) {
                ForEach(lost.indices, id: \.self) { i in point(lost[i].text, seg: lost[i].seg.first, mine: true) }
            }
        }
        if let step = n.nextStep {
            block("Next", icon: "arrow.right.circle.fill", tint: Theme.good) { point(step.text, seg: step.seg.first, mine: true) }
        }
        if !n.remember.isEmpty {
            block("Remember", icon: "star.fill", tint: Theme.warn) {
                ForEach(n.remember.indices, id: \.self) { i in point(n.remember[i].text, seg: n.remember[i].seg.first, mine: false) }
            }
        }
        ForEach(n.sections.indices, id: \.self) { i in
            let sec = n.sections[i]
            block(sec.heading, icon: "text.alignleft", tint: Theme.faint) {
                ForEach(sec.points.indices, id: \.self) { j in
                    let p = sec.points[j]
                    point(p.text, seg: p.seg.first, mine: p.mine == true)
                }
            }
        }
        HStack(spacing: 2) {
            IconButton("arrow.clockwise", help: "Make the notes again") { Task { await state.classes.regenerate(detail.session.id) } }
            IconButton("doc.on.doc", help: "Copy as Markdown") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(ClassVM.markdown(detail), forType: .string)
                Sound.shared.play(.done)
            }
            Spacer()
            IconButton("trash", help: "Delete this class") { confirmDelete() }
        }
        .padding(.top, 6)
    }

    /// A titled card of points.
    private func block<C: View>(_ title: String, icon: String, tint: Color, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(title, systemImage: icon).font(Theme.ui(12.5, 650)).foregroundStyle(tint == Theme.faint ? Theme.dim : tint)
            content()
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.raised).shadow(color: Theme.shadow, radius: 10, y: 4))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Theme.line))
    }

    private func point(_ text: String, seg: Int?, mine: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle().fill(mine ? Theme.ember : Theme.ink.opacity(0.25)).frame(width: 5, height: 5).offset(y: -2)
            Text(text).font(Theme.body).foregroundStyle(mine ? Theme.text : Theme.dim)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            Spacer(minLength: 8)
            chip(seg)
        }
    }

    @ViewBuilder private func chip(_ seg: Int?) -> some View {
        if let seg, let g = detail.segments.first(where: { $0.id == seg }) {
            Text(ClassTime.mmss(g.tStart)).font(Theme.ui(11, 600)).monospacedDigit().foregroundStyle(Theme.accent)
                .padding(.horizontal, 7).padding(.vertical, 2).background(Capsule().fill(Theme.emberSoft))
                .onTapGesture { showTranscript = true; at = seg }
        }
    }

    private func confirmDelete() {
        let a = NSAlert()
        a.messageText = "Delete this class?"
        a.informativeText = "Its transcript, notes and action items go, including the lines on Priorities."
        a.addButton(withTitle: "Delete")
        a.addButton(withTitle: "Cancel")
        if a.runModal() == .alertFirstButtonReturn { Task { await state.classes.delete(detail.session.id) } }
    }
}
