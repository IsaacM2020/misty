import SwiftUI

/// The Classes tab: every class by subject, newest first, with Ask across classes at the top, and one class's notes
/// on the right.
struct ClassesView: View {
    @Environment(AppState.self) private var state
    @State private var subject: String? = nil
    @State private var selecting = false
    @State private var picked: Set<String> = []
    @State private var sheet: ClassSheet? = nil

    var body: some View {
        let vm = state.classes
        let shown = vm.list.filter { subject == nil || $0.subject == subject }
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                PageHeader(title: "Classes") {
                    Button { Sound.shared.play(.thunk); ClassPanel.shared.show(state) } label: {
                        HStack(spacing: 6) {
                            Circle().fill(vm.isOpen ? Theme.good : Theme.urgent).frame(width: 7, height: 7)
                            Text(vm.isOpen ? "Open" : "Start")
                        }
                    }
                    .buttonStyle(KeyStyle(plastic: Theme.black, size: 12.5, hPad: 13, vPad: 6))
                    .focusEffectDisabled()
                    .help(vm.isOpen ? "Open the class panel" : "Start a class")
                    Button { toggleSelecting() } label: { Text(selecting ? "Done" : "Select") }
                        .buttonStyle(KeyStyle(plastic: selecting ? Theme.black : Theme.light, size: 12.5, hPad: 13, vPad: 6))
                        .focusEffectDisabled().help("Pick lessons to merge or delete")
                }
                SubjectFilter(subject: $subject).padding(.horizontal, Page.gutter).padding(.bottom, 4)
                if selecting {
                    SelectionBar(count: picked.count, canMerge: chosen(vm).filter(Self.canChange).count >= 2 && chosen(vm).allSatisfy(Self.canChange),
                                 canDelete: !chosen(vm).isEmpty && chosen(vm).allSatisfy(Self.canChange),
                                 merge: { sheet = .merge(chosen(vm)) }, delete: { sheet = .delete(chosen(vm)) })
                        .padding(.horizontal, Page.gutter - 6).padding(.bottom, 6)
                        .transition(.opacity)
                }
                AskBox(session: nil, subject: subject).padding(.horizontal, Page.gutter - 12)
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        if shown.isEmpty {
                            VStack(spacing: 10) {
                                Image(systemName: "waveform").font(.system(size: 22, weight: .medium)).foregroundStyle(Theme.faint)
                                Text("No classes yet").font(Theme.ui(14, 600)).foregroundStyle(Theme.dim)
                            }
                            .frame(maxWidth: .infinity).padding(.top, 30)
                        }
                        ForEach(shown) { c in
                            ClassRow(c: c, on: vm.detail?.session.id == c.id, selecting: selecting, ticked: picked.contains(c.id)) {
                                if selecting || NSEvent.modifierFlags.contains(.command) { tick(c.id) }
                                else { Task { await vm.open(c.id) } }
                            }
                            .contextMenu { menu(for: c, vm: vm) }
                        }
                    }
                    .padding(.horizontal, Page.gutter - 10).padding(.top, 4).padding(.bottom, 20)
                    .animation(Motion.or(Motion.morph), value: subject)
                }
            }
            .frame(width: 340)
            Theme.line.frame(width: 1)
            Group {
                if let d = vm.detail { ClassNotesView(detail: d).padding(.top, 14) }
                else {
                    VStack(spacing: 14) {
                        Orb(mood: .idle, size: 52).opacity(0.85)
                        Text("Pick a class").font(Theme.ui(15, 600)).foregroundStyle(Theme.dim)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .task { await vm.loadList() }
        .sheet(item: $sheet) { sh in
            switch sh {
            case .delete(let cs):
                DeleteClassesSheet(classes: cs) {
                    sheet = nil
                    Task { await vm.deleteMany(cs.map(\.id)); picked = []; selecting = false }
                } cancel: { sheet = nil }
            case .merge(let cs):
                MergeClassesSheet(classes: cs) { title in
                    sheet = nil
                    Task { await vm.merge(cs.map(\.id), title: title); picked = []; selecting = false }
                } cancel: { sheet = nil }
            }
        }
    }

    /// A class that is still being recorded can't be merged or deleted from here.
    static func canChange(_ c: ClassSession) -> Bool { c.status != "live" && c.status != "paused" }

    private func chosen(_ vm: ClassVM) -> [ClassSession] {
        vm.list.filter { picked.contains($0.id) }.sorted { $0.startedAt < $1.startedAt }
    }

    private func toggleSelecting() {
        withAnimation(Motion.or(Motion.quick)) { selecting.toggle(); if !selecting { picked = [] } }
    }

    private func tick(_ id: String) {
        withAnimation(Motion.or(Motion.quick)) {
            selecting = true
            if picked.contains(id) { picked.remove(id) } else { picked.insert(id) }
        }
    }

    /// Right-click menu. It acts on the picked lessons when this one is among them, else on this one alone.
    @ViewBuilder private func menu(for c: ClassSession, vm: ClassVM) -> some View {
        let set = (picked.contains(c.id) ? chosen(vm) : [c]).sorted { $0.startedAt < $1.startedAt }
        let ok = set.allSatisfy(Self.canChange)
        let n = set.count
        Button(picked.contains(c.id) ? "Unselect" : "Select") { tick(c.id) }
        Divider()
        Button(n >= 2 ? "Merge \(n) lessons…" : "Merge with…") {
            if n >= 2 { sheet = .merge(set) } else { tick(c.id) }          // alone: start picking the others
        }.disabled(!ok)
        Button(n >= 2 ? "Delete \(n) lessons…" : "Delete…", role: .destructive) { sheet = .delete(set) }.disabled(!ok)
    }
}

enum ClassSheet: Identifiable {
    case delete([ClassSession]), merge([ClassSession])
    var id: String {
        switch self {
        case .delete(let c): return "d" + c.map(\.id).joined()
        case .merge(let c): return "m" + c.map(\.id).joined()
        }
    }
}

/// The bar that appears while picking: how many, and the two things you can do with them.
private struct SelectionBar: View {
    let count: Int
    let canMerge: Bool
    let canDelete: Bool
    let merge: () -> Void
    let delete: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Text(count == 0 ? "Pick lessons" : "\(count) selected").font(Theme.ui(13, 600)).foregroundStyle(Theme.dim)
            Spacer(minLength: 6)
            Button(action: merge) { Label("Merge", systemImage: "arrow.triangle.merge") }
                .buttonStyle(KeyStyle(plastic: Theme.black, size: 12, hPad: 11, vPad: 5)).disabled(!canMerge).opacity(canMerge ? 1 : 0.4)
                .help("Merge the picked lessons into one")
            Button(action: delete) { Label("Delete", systemImage: "trash") }
                .buttonStyle(KeyStyle(plastic: Theme.light, size: 12, hPad: 11, vPad: 5)).disabled(!canDelete).opacity(canDelete ? 1 : 0.4)
                .help("Delete the picked lessons")
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.panel))
    }
}

private let sheetDay: DateFormatter = { let f = DateFormatter(); f.dateFormat = "EEE d MMM · h:mm a"; return f }()

private struct ClassLine: View {
    let c: ClassSession
    var body: some View {
        HStack(spacing: 10) {
            IconTile(icon: SubjectStyle.icon(c.subject), plastic: SubjectStyle.plastic(c.subject), side: 26)
            VStack(alignment: .leading, spacing: 0) {
                Text(c.title).font(Theme.ui(13, 600)).lineLimit(1)
                Text(sheetDay.string(from: Date(timeIntervalSince1970: c.startedAt))).font(Theme.small).foregroundStyle(Theme.faint)
            }
            Spacer(minLength: 0)
        }
    }
}

private struct DeleteClassesSheet: View {
    let classes: [ClassSession]
    let confirm: () -> Void
    let cancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(classes.count == 1 ? "Delete this lesson?" : "Delete \(classes.count) lessons?").font(Theme.title)
            ScrollView { VStack(alignment: .leading, spacing: 8) { ForEach(classes) { ClassLine(c: $0) } } }
                .frame(maxHeight: 180)
            Text("Their transcript, notes, action items, teacher links and lines on Priorities are deleted for good. Any parts merged into them go too. The audio moves to the Trash.")
                .font(Theme.small).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Cancel", action: cancel).buttonStyle(KeyStyle(plastic: Theme.light, size: 12.5, hPad: 14, vPad: 6))
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button(action: confirm) { Text("Delete") }
                    .buttonStyle(KeyStyle(plastic: Plastic(fill: Theme.urgent, edge: .clear, fg: .white), size: 12.5, hPad: 16, vPad: 6))
            }
        }
        .padding(22).frame(width: 440)
    }
}

private struct MergeClassesSheet: View {
    let classes: [ClassSession]                      // already in time order
    let confirm: (String?) -> Void
    let cancel: () -> Void
    @State private var title: String

    init(classes: [ClassSession], confirm: @escaping (String?) -> Void, cancel: @escaping () -> Void) {
        self.classes = classes; self.confirm = confirm; self.cancel = cancel
        _title = State(initialValue: classes.first?.title ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Merge \(classes.count) lessons?").font(Theme.title)
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(classes.enumerated()), id: \.element.id) { i, c in
                        HStack(spacing: 8) {
                            Text("\(i + 1)").font(Theme.ui(11, 700)).monospacedDigit().foregroundStyle(Theme.faint).frame(width: 14)
                            ClassLine(c: c)
                        }
                    }
                }
            }.frame(maxHeight: 180)
            VStack(alignment: .leading, spacing: 5) {
                Text("Name").font(Theme.small).foregroundStyle(Theme.faint)
                TextField("Name", text: $title).textFieldStyle(.roundedBorder)
            }
            Text("They join in time order as one lesson. It keeps the first one's date, the transcripts and your notes are joined, and the audio stays as ordered parts. The notes are made again from the whole lesson.")
                .font(Theme.small).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Cancel", action: cancel).buttonStyle(KeyStyle(plastic: Theme.light, size: 12.5, hPad: 14, vPad: 6))
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button { confirm(title == classes.first?.title ? nil : title) } label: { Text("Merge") }
                    .buttonStyle(KeyStyle(plastic: Theme.black, size: 12.5, hPad: 16, vPad: 6))
            }
        }
        .padding(22).frame(width: 440)
    }
}

/// Subject filter: a row of round icon keys. The chosen one fills with its colour; tap it again for all.
private struct SubjectFilter: View {
    @Binding var subject: String?
    var body: some View {
        HStack(spacing: 6) {
            ForEach(ClassVM.subjects, id: \.self) { s in
                let on = subject == s
                let w = SubjectStyle.plastic(s)
                Button { withAnimation(Motion.or(Motion.quick)) { subject = on ? nil : s } } label: {
                    Image(systemName: SubjectStyle.icon(s)).font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(on ? w.fg : Theme.dim)
                        .frame(width: 32, height: 32)
                        .background(Circle().fill(on ? w.fill : Theme.panel))
                        .overlay(Circle().strokeBorder(on ? w.fg.opacity(0.35) : .clear, lineWidth: 1.5))
                        .scaleEffect(on ? 1.08 : 1)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain).focusEffectDisabled().help(s).accessibilityLabel(s)
            }
        }
    }
}

/// One class in the list: its subject tile, the day, and a dot while notes are still being made.
private struct ClassRow: View {
    let c: ClassSession
    let on: Bool
    var selecting = false
    var ticked = false
    let open: () -> Void
    @State private var hovering = false

    private static let day: DateFormatter = { let f = DateFormatter(); f.dateFormat = "d MMM"; return f }()
    private static let week: DateFormatter = { let f = DateFormatter(); f.dateFormat = "EEE · h:mm a"; return f }()

    var body: some View {
        let date = Date(timeIntervalSince1970: c.startedAt)
        Button(action: open) {
            HStack(spacing: 12) {
                if selecting {
                    Image(systemName: ticked ? "checkmark.circle.fill" : "circle").font(.system(size: 16))
                        .foregroundStyle(ticked ? Theme.accent : Theme.faint).transition(.opacity)
                }
                IconTile(icon: SubjectStyle.icon(c.subject), plastic: SubjectStyle.plastic(c.subject), side: 34)
                VStack(alignment: .leading, spacing: 1) {
                    Text(Self.day.string(from: date)).font(Theme.ui(14, on ? 650 : 550))
                    Text(Self.week.string(from: date)).font(Theme.small).foregroundStyle(Theme.faint)
                }
                Spacer(minLength: 6)
                if c.status == "live" || c.status == "noting" {
                    Orb(mood: .thinking, size: 14)
                } else if c.status == "failed" || c.status == "too_little" {
                    Image(systemName: "exclamationmark.circle").font(.system(size: 12)).foregroundStyle(Theme.warn)
                }
            }
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(on || ticked ? Theme.raised : hovering ? Theme.hover : .clear)
                .shadow(color: Theme.shadow.opacity(on ? 1 : 0), radius: 8, y: 3))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(c.title + " · notes " + ClassCost.label(c.cost ?? 0))
    }
}
