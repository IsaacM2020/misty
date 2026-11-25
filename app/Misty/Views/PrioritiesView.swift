import SwiftUI

/// What matters this week: the standing priorities, what classes handed him, and the changes Kit suggests.
struct PrioritiesView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        let vm = state.priorities
        VStack(alignment: .leading, spacing: 0) {
            PageHeader(title: "Priorities")
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if vm.lines.isEmpty, vm.fromClass.isEmpty {
                        VStack(spacing: 12) {
                            Orb(mood: .thinking, size: 48)
                            Text("Still reading your week").font(Theme.ui(16, 650)).foregroundStyle(Theme.dim)
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 60)
                    }
                    if !vm.lines.isEmpty {
                        Card {
                            ForEach(Array(vm.lines.enumerated()), id: \.element.id) { i, l in
                                HStack(alignment: .center, spacing: 14) {
                                    KeyCap(text: "\(i + 1)", plastic: i == 0 ? Theme.limeFill : i < 3 ? Theme.limeSoft : Theme.light,
                                           size: i == 0 ? 16 : 14, side: i == 0 ? 38 : 32)
                                    Text(l.text).font(Theme.ui(i == 0 ? 16 : 14.5, i == 0 ? 650 : 550))
                                        .lineLimit(2).fixedSize(horizontal: false, vertical: true).help(l.text)
                                    Spacer(minLength: 12)
                                    if let a = l.area { AreaTag(area: a) }
                                }
                                .padding(.vertical, i == 0 ? 4 : 0)
                                .cardRow(last: i == vm.lines.count - 1)
                            }
                        }
                        .padding(.bottom, 26)
                    }
                    if !vm.fromClass.isEmpty {
                        FromClass(vm: vm).padding(.bottom, 26)
                    }
                    if !vm.diff.isEmpty {
                        HStack(spacing: 7) {
                            Orb(size: 16)
                            Text("EDITH suggests").font(Theme.ui(13, 650)).foregroundStyle(Theme.dim)
                        }
                        .padding(.leading, 4).padding(.bottom, 9)
                        Card {
                            ForEach(Array(vm.diff.enumerated()), id: \.element.id) { i, d in
                                let on = vm.accepted.contains(d.id)
                                Button { vm.toggle(d.id) } label: {
                                    let add = d.op == "add"
                                    HStack(alignment: .center, spacing: 11) {
                                        Image(systemName: add ? "plus" : "minus").font(.system(size: 11, weight: .heavy))
                                            .foregroundStyle(add ? Theme.good : Theme.urgent)
                                            .frame(width: 24, height: 24)
                                            .background(Circle().fill((add ? Theme.good : Theme.urgent).opacity(0.13)))
                                        Text(d.text).font(Theme.body).multilineTextAlignment(.leading)
                                            .foregroundStyle(on ? Theme.text : Theme.dim)
                                        Spacer(minLength: 8)
                                        Image(systemName: on ? "checkmark.circle.fill" : "circle")
                                            .font(.system(size: 18)).foregroundStyle(on ? Theme.ember : Theme.faint)
                                            .contentTransition(.symbolEffect(.replace))
                                    }
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .cardRow(last: i == vm.diff.count - 1)
                            }
                        }
                        HStack(spacing: 4) {
                            QuietButton("All") { vm.acceptAll() }
                            QuietButton("None") { vm.acceptNone() }
                            Spacer()
                            GoButton("Apply") { Task { await vm.apply() } }
                        }
                        .padding(.top, 12)
                    }
                }
                .pageColumn().padding(.top, 2).padding(.bottom, 28)
            }
        }
        .task { await vm.load() }
    }
}

/// A subject or area as a small coloured tag, the same colour every time for the same word.
private struct AreaTag: View {
    let area: String
    var body: some View {
        let w = SubjectStyle.plastic(area)
        Label(area, systemImage: SubjectStyle.icon(area)).font(Theme.ui(11.5, 650)).foregroundStyle(w.fg).lineLimit(1)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Capsule().fill(w.fill))
    }
}

/// What classes handed him, one small card per subject. The subject prefix ("Chem: ") becomes the card's tag, and what
/// he has ticked folds away into one row at the end.
private struct FromClass: View {
    let vm: PrioritiesVM
    @State private var showDone = false
    @State private var expanded: Set<String> = []

    static func split(_ text: String) -> (subject: String?, rest: String) {
        guard let r = text.range(of: ": "), text.distance(from: text.startIndex, to: r.lowerBound) <= 14 else { return (nil, text) }
        return (String(text[..<r.lowerBound]), String(text[r.upperBound...]))
    }

    var body: some View {
        let open = vm.fromClass.filter { $0.done == nil }
        let done = vm.fromClass.filter { $0.done != nil }
        let subjects = open.reduce(into: [String]()) { acc, c in
            let s = Self.split(c.text).subject ?? "Other"
            if !acc.contains(s) { acc.append(s) }
        }
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 7) {
                Image(systemName: "graduationcap.fill").foregroundStyle(Theme.accent)
                Text("From class").foregroundStyle(Theme.dim)
                Chip("\(open.count)")
            }
            .font(Theme.ui(13, 650)).padding(.leading, 4)
            ForEach(subjects, id: \.self) { subj in
                let all = open.filter { (Self.split($0.text).subject ?? "Other") == subj }
                let full = expanded.contains(subj)
                let rows = full ? all : Array(all.prefix(3))
                Card {
                    HStack {
                        AreaTag(area: subj)
                        Spacer()
                        if all.count > 3 {
                            Button { withAnimation(Motion.or(Motion.morph)) {
                                if full { expanded.remove(subj) } else { expanded.insert(subj) } } } label: {
                                Text(full ? "Less" : "+\(all.count - 3)").font(Theme.ui(12, 650)).foregroundStyle(Theme.accent)
                                    .padding(.horizontal, 9).padding(.vertical, 3).background(Capsule().fill(Theme.emberSoft))
                            }
                            .buttonStyle(.plain).focusEffectDisabled()
                        }
                    }
                    .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 2)
                    ForEach(Array(rows.enumerated()), id: \.element.id) { i, c in
                        Item(vm: vm, line: c).cardRow(last: i == rows.count - 1)
                    }
                }
            }
            if !done.isEmpty {
                Button { withAnimation(Motion.or(Motion.morph)) { showDone.toggle() } } label: {
                    HStack(spacing: 7) {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.ember)
                        Text("\(done.count) done")
                        Image(systemName: "chevron.down").font(.system(size: 9.5, weight: .bold)).rotationEffect(.degrees(showDone ? 180 : 0))
                    }
                    .font(Theme.ui(12.5, 600)).foregroundStyle(Theme.dim)
                    .padding(.horizontal, 11).padding(.vertical, 6).background(Capsule().fill(Theme.panel))
                }
                .buttonStyle(.plain).focusEffectDisabled().padding(.leading, 2)
                if showDone {
                    Card {
                        ForEach(Array(done.enumerated()), id: \.element.id) { i, c in
                            Item(vm: vm, line: c).cardRow(last: i == done.count - 1)
                        }
                    }
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
        }
    }

    private struct Item: View {
        let vm: PrioritiesVM
        let line: ClassPriorityLine
        var body: some View {
            let done = line.done != nil
            Button { if !done { Sound.shared.play(.done) }; Task { await vm.setDone(line.id, !done) } } label: {
                HStack(alignment: .center, spacing: 11) {
                    Image(systemName: done ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 18)).foregroundStyle(done ? Theme.ember : Theme.faint)
                        .contentTransition(.symbolEffect(.replace))
                    Text(FromClass.split(line.text).rest).font(Theme.body).foregroundStyle(done ? Theme.faint : Theme.text)
                        .strikethrough(done).multilineTextAlignment(.leading).lineLimit(2).help(line.text)
                    Spacer(minLength: 12)
                    if let d = line.due { Chip(Pretty.day(d)) }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }
}
