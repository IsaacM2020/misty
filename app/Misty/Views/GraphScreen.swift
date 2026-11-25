import SwiftUI
import WebKit

struct GraphScreen: View {
    @Environment(AppState.self) private var state
    @State private var selected: String?
    @State private var page: PageDetail?
    @State private var graph: PreparedGraph?
    @State private var view = GraphViewState()
    @State private var query = ""
    @State private var matches: [GraphNode] = []
    @State private var showSearch = false

    var body: some View {
        HStack(spacing: 0) {
            ZStack(alignment: .topLeading) {
                GraphWebView(graph: graph, focus: view.focusId) { event in
                    switch event {
                    case .area(let id):
                        view.toggle(id)
                        Task { await load() }
                    case .more(let areaId):
                        let label = graph?.nodes.first { $0.id == areaId }?.label ?? ""
                        query = label
                        showSearch = true
                        Task { await runSearch(); }
                    case .node(let id):
                        selected = id
                        Task { await loadPage(id) }
                    }
                }
                if graph == nil {
                    Orb(mood: .thinking, size: 48).frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                searchBar
            }
            if let p = page {
                Divider()
                PagePanel(page: p, onClose: { page = nil; selected = nil }, onOpen: { path in
                    Task { await loadPage(String(path.dropLast(path.hasSuffix(".md") ? 3 : 0))) }
                }, onChanged: { Task { await load(); if let s = selected { await loadPage(s) } } })
                .frame(width: 340)
            }
        }
        .task { await load() }
    }

    private var searchBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").foregroundStyle(Theme.dim)
                TextField("Find", text: $query)
                    .textFieldStyle(.plain)
                    .onSubmit { Task { await runSearch() } }
                if !query.isEmpty {
                    Button { query = ""; matches = []; showSearch = false; view.search(""); view.focus(nil)
                             Task { await load() } } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(Theme.dim)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(Capsule().fill(Theme.raised).shadow(color: Theme.shadow, radius: 10, y: 4))
            .overlay(Capsule().strokeBorder(Theme.line))
            .frame(width: 240)
            if showSearch && !matches.isEmpty {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(matches.prefix(30), id: \.id) { n in
                            SearchResultRow(node: n) {
                                view.focus(n.id)
                                Task { await load(); await loadPage(n.id) }
                            }
                        }
                        if matches.count > 30 {
                            Text("+\(matches.count - 30)")
                                .font(Theme.small).foregroundStyle(Theme.dim).padding(8)
                        }
                    }
                }
                .frame(width: 280)
                .frame(maxHeight: 280)
                .fixedSize(horizontal: false, vertical: true)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.raised).shadow(color: Theme.shadow, radius: 12, y: 5))
            }
        }
        .padding(12)
        .task(id: query) {                       // search as you type, after a short pause
            let q = query.trimmingCharacters(in: .whitespaces)
            if q.isEmpty {
                guard showSearch else { return }
                matches = []; showSearch = false; view.search(""); await load()
                return
            }
            try? await Task.sleep(for: .milliseconds(250))
            if Task.isCancelled { return }
            await runSearch()
        }
    }

    private func runSearch() async {
        view.search(query)
        showSearch = !view.query.isEmpty
        guard let g = await fetch(view.params) else { return }
        matches = g.nodes.filter { $0.type != "self" && $0.type != "area" && $0.type != "group" }
        await load()
    }

    private func fetch(_ p: GraphParams) async -> GraphData? {
        try? await state.live.call("graph.get", p)
    }

    private func load() async {
        if let g = await fetch(view.params) { graph = GraphLayout.prepare(g) } else { graph = nil }
    }

    private func loadPage(_ id: String) async {
        guard id != "me", !id.hasPrefix("group/") else { return }
        struct P: Encodable, Sendable { var path: String }
        do { page = try await state.live.call("page.get", P(path: id.hasSuffix(".md") ? id : id + ".md")) }
        catch { page = nil }
    }
}

private struct SearchResultRow: View {
    let node: GraphNode
    let action: () -> Void
    @State private var hovering = false
    var body: some View {
        Button(action: action) {
            HStack {
                Text(node.label).lineLimit(1)
                Spacer(minLength: 8)
                Text(node.type.capitalized).font(Theme.ui(11, 600)).foregroundStyle(Theme.faint)
            }
            .padding(.vertical, 6).padding(.horizontal, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(hovering ? Theme.hover : .clear)
        .onHover { hovering = $0 }
    }
}

enum GraphEvent { case node(String), area(String), more(String) }

struct GraphWebView: NSViewRepresentable {
    let graph: PreparedGraph?
    let focus: String?
    let onNode: (GraphEvent) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onNode: onNode) }

    func makeNSView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(context.coordinator, name: "misty")
        let web = WKWebView(frame: .zero, configuration: cfg)
        web.setValue(false, forKey: "drawsBackground")
        if let url = Bundle.main.url(forResource: "graph", withExtension: "html") {
            web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
        context.coordinator.web = web
        return web
    }

    func updateNSView(_ web: WKWebView, context: Context) {
        context.coordinator.onNode = onNode
        guard let g = graph else { return }
        let nodes = g.nodes.map { n -> [String: Any] in
            ["id": n.id, "label": n.label, "type": n.type, "area": n.area as Any? ?? NSNull(),
             "importance": n.importance, "glow": n.glow, "radius": n.radius, "fixed": n.fixed,
             "x": n.x, "y": n.y, "areaX": n.areaX, "areaY": n.areaY, "more": n.more, "expanded": n.expanded,
             "members": n.members, "sources": n.sources]
        }
        let edges = g.edges.map { e -> [String: Any] in
            ["source": e.source, "target": e.target, "kind": e.kind, "weight": e.weight as Any? ?? NSNull()]
        }
        if let data = try? JSONSerialization.data(withJSONObject: ["nodes": nodes, "edges": edges]),
           let json = String(data: data, encoding: .utf8) {
            context.coordinator.pending = "window.setGraph(\(json))"
            context.coordinator.flush()
        }
        if let f = focus { web.evaluateJavaScript("window.focusNode(\(jsString(f)))") }
    }

    private func jsString(_ s: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [s])
        let arr = String(data: data ?? Data("[\"\"]".utf8), encoding: .utf8) ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())
    }

    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        var onNode: (GraphEvent) -> Void
        weak var web: WKWebView?
        var pending: String?
        init(onNode: @escaping (GraphEvent) -> Void) { self.onNode = onNode }

        func flush() {
            guard let js = pending, let web else { return }
            web.evaluateJavaScript(js) { [weak self] _, err in
                if err == nil { self?.pending = nil }
            }
        }

        func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
            guard let d = m.body as? [String: Any], let id = d["id"] as? String else { return }
            let event: GraphEvent
            switch d["type"] as? String {
            case "area": event = .area(id)
            case "more": event = .more(id)
            case "node": event = .node(id)
            default: return
            }
            DispatchQueue.main.async { self.onNode(event) }
        }
    }
}
