import SwiftUI

/// What she cares about on his behalf. View only: he adds, changes and removes things by telling her (typing or by
/// voice). Also shows what she is following and what she told him on her own, where a thumb is the one thing he can tap.
struct RadarView: View {
    @State private var openTold: Set<String> = []     // Told you rows tapped open to show the whole line
    @Environment(AppState.self) private var state

    var body: some View {
        let vm = state.radar
        VStack(alignment: .leading, spacing: 0) {
            PageHeader(title: "Radar") {
                Label("Tell me to change it", systemImage: "bubble.left.fill")
                    .font(Theme.ui(12, 600)).foregroundStyle(Theme.accent)
                    .padding(.horizontal, 10).padding(.vertical, 5).background(Capsule().fill(Theme.emberSoft))
                    .help("Say it in Chat or out loud: \u{201C}I care about chess\u{201D}, \u{201C}stop caring about football\u{201D}.")
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if vm.loaded && vm.cares.isEmpty && vm.watching.isEmpty {
                        VStack(spacing: 12) {
                            Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 26, weight: .medium)).foregroundStyle(Theme.accent)
                                .symbolEffect(.variableColor.iterative, options: .repeating)
                            Text("Nothing on the radar yet").font(Theme.ui(15, 600)).foregroundStyle(Theme.dim)
                        }
                        .frame(maxWidth: .infinity).padding(.top, 80)
                    }
                    if !vm.watching.isEmpty {
                        section("Watching", icon: "eye.fill") {
                            ForEach(Array(vm.watching.enumerated()), id: \.element.id) { i, w in
                                watchRow(w).cardRow(last: i == vm.watching.count - 1)
                            }
                        }
                    }
                    if !vm.told.isEmpty {
                        section("Told you", icon: "bell.fill", note: vm.held > 0 ? "\(vm.held) held" : nil) {
                            ForEach(Array(vm.told.enumerated()), id: \.element.id) { i, t in
                                toldRow(t, vm).cardRow(last: i == vm.told.count - 1)
                            }
                        }
                    }
                    ForEach(RadarVM.groups, id: \.kind) { g in
                        let rows = vm.care(in: g.kind)
                        if !rows.isEmpty {
                            section(g.title, icon: Self.groupIcon(g.kind)) {
                                ForEach(Array(rows.enumerated()), id: \.element.id) { i, c in
                                    row(c).cardRow(last: i == rows.count - 1)
                                }
                            }
                        }
                    }
                    if !vm.ways.isEmpty {
                        section("How she is with you", icon: "slider.horizontal.3") {
                            ForEach(Array(vm.ways.enumerated()), id: \.element.id) { i, w in
                                HStack(alignment: .firstTextBaseline, spacing: 12) {
                                    Text(w.text).font(Theme.body).fixedSize(horizontal: false, vertical: true)
                                    Spacer(minLength: 12)
                                    if w.isDefault { Chip("default") }
                                }
                                .cardRow()
                            }
                            Text("Change these by telling her.").font(Theme.small).foregroundStyle(Theme.faint)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                        }
                    }
                    if !vm.did.isEmpty {
                        section("Did for you", icon: "checkmark.circle") {
                            ForEach(Array(vm.did.enumerated()), id: \.element.id) { i, d in
                                HStack(alignment: .firstTextBaseline, spacing: 12) {
                                    Image(systemName: d.isVerified ? "checkmark" : "minus").foregroundStyle(d.isVerified ? Theme.accent : Theme.faint)
                                        .frame(width: 16)
                                    Text(d.what.isEmpty ? d.title : d.what).font(Theme.body).fixedSize(horizontal: false, vertical: true)
                                    Spacer(minLength: 12)
                                    if d.undoable { Chip("can undo") }
                                }
                                .cardRow(last: i == vm.did.count - 1)
                            }
                            Text("Say \"undo that\" to take the last one back.").font(Theme.small).foregroundStyle(Theme.faint)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                        }
                    }
                    if !vm.protocols.isEmpty {
                        section("Protocols", icon: "list.number") {
                            ForEach(Array(vm.protocols.enumerated()), id: \.element.id) { i, p in
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack(spacing: 8) {
                                        Text(p.key.capitalized).font(Theme.body)
                                        Chip(p.runs == 1 ? "ran once" : "ran \(p.runs) times")
                                    }
                                    Text("Say: " + p.triggers.map { "\u{201C}\($0)\u{201D}" }.joined(separator: ", ")).font(Theme.small).foregroundStyle(Theme.dim)
                                    Text(p.steps.enumerated().map { "\($0.offset + 1). \($0.element)" }.joined(separator: "  ")).font(Theme.small)
                                        .foregroundStyle(Theme.faint).fixedSize(horizontal: false, vertical: true)
                                }
                                .cardRow(last: i == vm.protocols.count - 1)
                            }
                            Text("Change these by telling her.").font(Theme.small).foregroundStyle(Theme.faint)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                        }
                    }
                    if let l = vm.learning {
                        learningSections(l)
                    }
                    if !vm.privateApps.isEmpty {
                        section("What I can't see", icon: "eye.slash") {
                            Text(vm.privateApps.joined(separator: ", ")).font(Theme.body).fixedSize(horizontal: false, vertical: true)
                                .cardRow()
                            Text("Change these by telling her.").font(Theme.small).foregroundStyle(Theme.faint)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                        }
                    }
                    if !vm.lessons.isEmpty {
                        section("Lessons", icon: "lightbulb.fill") {
                            ForEach(Array(vm.lessons.enumerated()), id: \.element.id) { i, l in
                                HStack(alignment: .firstTextBaseline, spacing: 12) {
                                    Text("\(l.n)").font(Theme.ui(12, 600)).foregroundStyle(Theme.faint).monospacedDigit()
                                        .frame(width: 16, alignment: .trailing)
                                    Text(l.text).font(Theme.body).fixedSize(horizontal: false, vertical: true)
                                    Spacer(minLength: 0)
                                }
                                .cardRow(last: i == vm.lessons.count - 1)
                            }
                        }
                    }
                }
                .pageColumn().padding(.top, 2).padding(.bottom, 28)
            }
        }
        .task { await vm.load() }
    }

    @ViewBuilder
    private func learningSections(_ l: Learning) -> some View {
        if !l.courses.isEmpty {
            section("Learning", icon: "graduationcap.fill") {
                ForEach(Array(l.courses.enumerated()), id: \.element.id) { i, c in courseRow(c).cardRow(last: i == l.courses.count - 1) }
                Text("Change a course by telling her.").font(Theme.small).foregroundStyle(Theme.faint)
                    .padding(.horizontal, 14).padding(.vertical, 9)
            }
        }
        if !l.shaky.isEmpty {
            section("Shaky", icon: "exclamationmark.triangle") {
                ForEach(Array(l.shaky.enumerated()), id: \.element.id) { i, t in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) { Text(t.topic).font(Theme.body); Chip(t.level) }
                        if let why = t.why, !why.isEmpty { Text(why.prefix(1).uppercased() + why.dropFirst()).font(Theme.small).foregroundStyle(Theme.dim) }
                    }
                    .cardRow(last: i == l.shaky.count - 1)
                }
            }
        }
        if !l.due.isEmpty {
            section("Due now", icon: "clock.badge.exclamationmark") {
                ForEach(Array(l.due.enumerated()), id: \.element.id) { i, t in
                    HStack(spacing: 8) { Text(t.topic).font(Theme.body); Spacer(minLength: 12); Chip(t.level) }
                        .cardRow(last: i == l.due.count - 1)
                }
            }
        }
        if !l.probes.isEmpty {
            section("Before and after", icon: "chart.line.uptrend.xyaxis") {
                ForEach(Array(l.probes.enumerated()), id: \.element.id) { i, p in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(p.topic).font(Theme.body)
                        Spacer(minLength: 12)
                        Text(p.line).font(Theme.small).foregroundStyle(Theme.dim)
                    }
                    .cardRow(last: i == l.probes.count - 1)
                }
            }
        }
        if l.drills.count > 0 || l.mistakes.total > 0 {
            section("This week", icon: "checklist") {
                if l.drills.count > 0 {
                    Text(LearningWords.drills(l.drills)).font(Theme.body).fixedSize(horizontal: false, vertical: true)
                        .cardRow(last: l.mistakes.total == 0)
                }
                if l.mistakes.total > 0 {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(LearningWords.mistakes(l.mistakes)).font(Theme.body).fixedSize(horizontal: false, vertical: true)
                        if !l.mistakes.pattern.isEmpty { Text(l.mistakes.pattern).font(Theme.small).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true) }
                    }
                    .cardRow(last: true)
                }
            }
        }
    }

    private func courseRow(_ c: LearningCourse) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(c.subject).font(Theme.ui(14, 600))
                if c.status == "paused" { Chip("paused") }
                Spacer(minLength: 12)
                Text(LearningWords.when(c)).font(Theme.small).foregroundStyle(Theme.dim)
            }
            if !c.goal.isEmpty { Text(c.goal).font(Theme.small).foregroundStyle(Theme.dim) }
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.line)
                    Capsule().fill(Theme.accent).frame(width: max(c.solid > 0 ? 6 : 0, g.size.width * c.fraction))
                }
            }
            .frame(height: 6)
            HStack(spacing: 6) {
                Text("\(c.solid) of \(c.topics) solid").font(Theme.small).foregroundStyle(Theme.dim)
                if c.shaky > 0 { Chip("\(c.shaky) shaky") }
                if c.due > 0 { Chip("\(c.due) due") }
            }
            if let n = c.next {
                Text(LearningWords.next(n)).font(Theme.small).foregroundStyle(Theme.faint)
            }
        }
    }

    static func groupIcon(_ kind: String) -> String {
        switch kind {
        case "goal": return "flag.fill"
        case "interest": return "sparkles"
        case "person": return "person.2.fill"
        default: return "circle.grid.2x2.fill"
        }
    }

    private func section<C: View>(_ title: String, icon: String, note: String? = nil, @ViewBuilder _ rows: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 7) {
                Image(systemName: icon).foregroundStyle(Theme.accent)
                Text(title).foregroundStyle(Theme.dim)
                if let note { Chip(note) }
            }
            .font(Theme.ui(13, 650))
            .padding(.leading, 4)
            Card { rows() }
        }
        .padding(.bottom, 24)
    }

    private func watchRow(_ w: Watch) -> some View {
        HStack(alignment: .top, spacing: 12) {
            IconTile(icon: "eye", plastic: Theme.limeSoft, side: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text(w.text).font(Theme.ui(14, 550)).lineLimit(1).help(w.text)
                if let e = w.lastError {
                    Label("Check failed", systemImage: "exclamationmark.triangle.fill").font(Theme.small).foregroundStyle(Theme.warn).help(e)
                } else if let l = w.latest {
                    Text(l).font(Theme.small).foregroundStyle(Theme.dim).lineLimit(1).help(l)
                }
            }
            Spacer(minLength: 12)
            Label(Pretty.every(w.everyMin).replacingOccurrences(of: "every ", with: ""), systemImage: "clock")
                .font(Theme.ui(11.5, 600)).foregroundStyle(Theme.dim).lineLimit(1).fixedSize()
                .padding(.horizontal, 8).padding(.vertical, 3).background(Capsule().fill(Theme.panel))
                .help("\(Pretty.every(w.everyMin)), until \(w.until)")
        }
    }

    private func toldRow(_ t: Told, _ vm: RadarVM) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(t.said).font(Theme.body).lineLimit(openTold.contains(t.id) ? nil : 1)
                    .fixedSize(horizontal: false, vertical: openTold.contains(t.id)).help(t.said)
                HStack(spacing: 5) {
                    Image(systemName: t.how == "card" ? "rectangle.on.rectangle" : t.how == "ask" ? "questionmark.bubble" : "speaker.wave.2")
                    Text(Self.when(t.at))
                    if t.feedback == "ignored" { Image(systemName: "eye.slash") }
                }
                .font(Theme.small).foregroundStyle(Theme.faint)
            }
            .contentShape(Rectangle())
            .onTapGesture {                                  // tap the line to read all of it, tap again to fold it
                withAnimation(.easeOut(duration: 0.15)) {
                    if openTold.contains(t.id) { openTold.remove(t.id) } else { openTold.insert(t.id) }
                }
            }
            Spacer(minLength: 12)
            HStack(spacing: 2) {
                IconButton(t.feedback == "up" || t.feedback == "accepted" ? "hand.thumbsup.fill" : "hand.thumbsup",
                           on: t.feedback == "up" || t.feedback == "accepted", help: "Good call") { Task { await vm.rate(t.id, "up") } }
                IconButton(t.feedback == "down" ? "hand.thumbsdown.fill" : "hand.thumbsdown", on: t.feedback == "down",
                           help: "Don't tell me this") { Task { await vm.rate(t.id, "down") } }
            }
        }
    }

    private static func when(_ at: Double) -> String {
        let f = DateFormatter()
        f.dateFormat = "EEE h:mm a"
        f.timeZone = TimeZone(identifier: "Asia/Singapore")
        return f.string(from: Date(timeIntervalSince1970: at))
    }

    private func row(_ c: Care) -> some View {
        HStack(alignment: .center, spacing: 12) {
            Text(c.key.prefix(1).uppercased() + c.key.dropFirst()).font(Theme.ui(14, 550)).help(c.text)
            Spacer(minLength: 12)
            if c.weight == 0 {
                Image(systemName: "nosign").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.urgent).help(c.level)
            } else {
                HStack(spacing: 3) {                          // how much she cares, as up to three bars
                    ForEach(1...3, id: \.self) { i in
                        Capsule().fill(i <= c.weight ? Theme.ember : Theme.line).frame(width: 5, height: CGFloat(6 + i * 3))
                    }
                }
                .frame(height: 15, alignment: .bottom).help(c.level)
            }
        }
    }
}
