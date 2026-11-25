import SwiftUI

struct PagePanel: View {
    @Environment(AppState.self) private var state
    let page: PageDetail
    let onClose: () -> Void
    let onOpen: (String) -> Void
    var onChanged: () -> Void = {}
    @State private var correction = ""
    @State private var sent = false
    @State private var sameQuery = ""
    @State private var sameResults: [LookupCandidate] = []
    @State private var merges: [MergeRow] = []
    @State private var note: String?

    private var entityId: String? { page.fm["entity_id"]?.stringValue }

    private var title: String { page.fm["title"]?.stringValue ?? page.path }

    private var recentLines: [String] {
        guard let r = page.body.range(of: "## Timeline\n") else { return [] }
        let rest = page.body[r.upperBound...]
        let end = rest.range(of: "\n## ")?.lowerBound ?? rest.endIndex
        return rest[..<end].split(separator: "\n").map(String.init).filter { $0.hasPrefix("- ") }.prefix(5).map { $0 }
    }

    private var rendered: AttributedString {
        let md = page.body.replacingOccurrences(of: "\\[\\[([^\\]|]+)(?:\\|[^\\]]*)?\\]\\]",
                                                with: "[$1](misty-page://$1)", options: .regularExpression)
        return (try? AttributedString(markdown: md, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(page.body)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text(title).font(Theme.title)
                    Spacer()
                    Button { onClose() } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                }
                Text(rendered)
                    .textSelection(.enabled)
                    .environment(\.openURL, OpenURLAction { url in
                        if url.scheme == "misty-page" {
                            onOpen((url.host() ?? "") + url.path)
                            return .handled
                        }
                        return .systemAction
                    })
                if !recentLines.isEmpty {
                    Text("Latest").font(Theme.title)
                    ForEach(recentLines, id: \.self) { Text($0).font(Theme.body).foregroundStyle(Theme.dim) }
                }
                if !page.sources.isEmpty {
                    Text("Sources").font(Theme.title)
                    ForEach(page.sources, id: \.itemId) { s in
                        Button {
                            if s.locator.hasPrefix("/") { NSWorkspace.shared.open(URL(fileURLWithPath: s.locator)) }
                        } label: { Label(s.title, systemImage: "doc.text") }.buttonStyle(.link)
                    }
                }
                if let me = entityId {
                    Divider()
                    Text("This is the same as...").font(Theme.title)
                    HStack {
                        TextField("Another name", text: $sameQuery).textFieldStyle(.roundedBorder)
                            .onSubmit { Task { await findSame(me) } }
                        Button("Find") { Task { await findSame(me) } }
                            .disabled(sameQuery.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                    ForEach(sameResults.filter { $0.id != me }) { c in
                        HStack {
                            VStack(alignment: .leading) { Text(c.name); Text(c.type).font(Theme.small).foregroundStyle(Theme.dim) }
                            Spacer()
                            Button("Merge into this page") { Task { await merge(keep: me, drop: c.id) } }
                        }
                    }
                    if let note { Text(note).font(Theme.small).foregroundStyle(Theme.dim) }
                    ForEach(merges.filter { $0.kept == me && $0.undone == 0 }) { m in
                        HStack {
                            Text("Merged \(m.droppedName)").font(Theme.body)
                            Spacer()
                            Button("Undo merge") { Task { await undo(m.id) } }
                        }
                    }
                }
                Divider()
                Text("This is wrong").font(Theme.title)
                TextField("What should it say?", text: $correction, axis: .vertical).textFieldStyle(.roundedBorder)
                HStack {
                    Button("Send correction") {
                        struct P: Encodable, Sendable { var path: String; var text: String }
                        let p = P(path: page.path, text: correction)
                        Task {
                            let _: JSONValue? = try? await state.live.call("correction.file", p)
                            sent = true; correction = ""
                        }
                    }
                    .disabled(correction.trimmingCharacters(in: .whitespaces).isEmpty)
                    if sent { Text("Filed. It is applied the next time this page is updated.").font(Theme.small).foregroundStyle(Theme.dim) }
                }
            }
            .padding(16)
        }
        .task(id: page.path) { await loadMerges() }
    }

    private func findSame(_ me: String) async {
        struct P: Encodable, Sendable { var text: String; var k: Int }
        let r: LookupResult? = try? await state.live.call("entities.lookup", P(text: sameQuery, k: 6))
        sameResults = r?.candidates ?? []
        note = sameResults.isEmpty ? "Nothing found for that name." : nil
    }

    private func merge(keep: String, drop: String) async {
        struct P: Encodable, Sendable { var keep: String; var drop: String }
        do {
            let _: JSONValue = try await state.live.call("entity.merge", P(keep: keep, drop: drop))
            sameResults = []; sameQuery = ""; note = "Merged. You can undo it below."
            await loadMerges()
            onChanged()
        } catch { note = "Could not merge: \(error)" }
    }

    private func undo(_ id: Int) async {
        struct P: Encodable, Sendable { var mergeId: Int }
        do {
            let _: JSONValue = try await state.live.call("entity.unmerge", P(mergeId: id))
            note = "Merge undone."
            await loadMerges()
            onChanged()
        } catch { note = "Could not undo: \(error)" }
    }

    private func loadMerges() async {
        struct P: Encodable, Sendable { var limit: Int }
        let l: MergeList? = try? await state.live.call("entity.list_merges", P(limit: 20))
        merges = l?.merges ?? []
    }
}
