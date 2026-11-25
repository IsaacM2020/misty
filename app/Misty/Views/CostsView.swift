import SwiftUI

/// Where the money goes: today and the week in plain job names, then every recent reply with the model calls inside it.
struct CostsView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @State private var vm: CostsVM?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("What EDITH costs").font(Theme.title)
                Spacer()
                Picker("", selection: Binding(get: { vm?.days ?? 7 }, set: { d in vm?.days = d; Task { await vm?.load() } })) {
                    Text("Today").tag(1); Text("7 days").tag(7); Text("30 days").tag(30)
                }
                .pickerStyle(.segmented).frame(width: 220)
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(20)
            if let r = vm?.report {
                ScrollView { content(r).padding(.horizontal, 20).padding(.bottom, 24) }
            } else if vm?.failed == true {
                Text("Couldn't reach EDITH's engine.").foregroundStyle(Theme.dim).padding(40)
            } else {
                ProgressView().padding(40)
            }
        }
        .frame(width: 680, height: 640)
        .background(Theme.bg)
        .task {
            let v = CostsVM(api: state.live)
            vm = v
            await v.load()
        }
    }

    @ViewBuilder private func content(_ r: CostReport) -> some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(spacing: 28) {
                stat("Today", r.today.usdText)
                stat(r.days == 1 ? "Today (window)" : "Last \(r.days) days", r.window.usdText)
            }
            section("Where it goes") {
                ForEach(r.byPurpose) { g in
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(g.label).font(Theme.body)
                            Text("\(g.calls) calls · \(g.models.joined(separator: ", "))").font(Theme.small).foregroundStyle(Theme.faint)
                        }
                        Spacer()
                        Text(g.usd.usdText).font(Theme.body).monospacedDigit()
                    }
                }
            }
            if r.byDay.count > 1 {
                section("By day") {
                    ForEach(r.byDay) { d in
                        HStack { Text(d.day).font(Theme.body); Spacer(); Text(d.usd.usdText).font(Theme.body).monospacedDigit() }
                    }
                }
            }
            section("Newest replies, call by call") {
                ForEach(r.messages) { m in reply(m) }
                Text("A reply is one or more model calls. Each call re-reads the rules, your facts and the chat so far; the cached part is billed at a tenth. A tool that spends its own money (Stream, class answers) is listed under the call that asked for it.")
                    .font(Theme.small).foregroundStyle(Theme.faint)
            }
        }
    }

    private func reply(_ m: CostReport.Reply) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 4) {
                if m.rounds.isEmpty {
                    Text("From before call-by-call tracking: only the total was saved.").font(Theme.small).foregroundStyle(Theme.faint)
                }
                ForEach(Array(m.rounds.enumerated()), id: \.offset) { i, rd in
                    HStack(alignment: .firstTextBaseline) {
                        Text("Call \(i + 1)").font(Theme.label).frame(width: 52, alignment: .leading)
                        Text("\(rd.in) in (\(rd.cached) cached) · \(rd.out) out" + ((rd.tools ?? []).isEmpty ? "" : " · asked for \((rd.tools ?? []).joined(separator: ", "))"))
                            .font(Theme.small).foregroundStyle(Theme.dim)
                        Spacer()
                        Text(rd.usd.usdText).font(Theme.small).monospacedDigit()
                    }
                }
                ForEach(Array(m.toolUsd.enumerated()), id: \.offset) { _, t in
                    HStack {
                        Text(t.tool).font(Theme.label).frame(width: 52, alignment: .leading)
                        Text("the tool's own model calls").font(Theme.small).foregroundStyle(Theme.dim)
                        Spacer()
                        Text(t.usd.usdText).font(Theme.small).monospacedDigit()
                    }
                }
            }
            .padding(.top, 4)
        } label: {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(m.question.isEmpty ? "(no question saved)" : m.question).font(Theme.body).lineLimit(1)
                    Text(m.at + (m.tools.isEmpty ? "" : " · " + m.tools.joined(separator: ", "))).font(Theme.small).foregroundStyle(Theme.faint)
                }
                Spacer()
                Text(m.usd.usdText).font(Theme.body).monospacedDigit()
            }
        }
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(Theme.label).foregroundStyle(Theme.dim)
            Text(value).font(Theme.headline)
        }
    }

    private func section<C: View>(_ title: String, @ViewBuilder _ body: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(Theme.label).foregroundStyle(Theme.dim)
            VStack(alignment: .leading, spacing: 10) { body() }
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.raised, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
    }
}
