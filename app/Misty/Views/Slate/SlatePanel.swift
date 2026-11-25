import AppKit
import SwiftUI
import WebKit

/// The Slate window: its own paper-and-ink window. The web view is made when it opens and freed when it closes,
/// so a closed Slate costs nothing. A standard window (not a floating panel).
@MainActor
final class SlatePanel: NSObject, NSWindowDelegate {
    static let shared = SlatePanel()
    private var window: NSWindow?

    func show(_ state: AppState) {
        let w = window ?? make(state)
        window = w
        if w.contentView == nil || !(w.contentView is NSHostingView<SlateWebView>) {
            w.contentView = NSHostingView(rootView: SlateWebView(vm: state.slate))
        }
        if !w.isVisible { place(w) }
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)      // a real window: comes to the front like any app
        state.slate.onClose = { [weak self] in self?.close() }
    }

    func close() {
        guard let w = window else { return }
        w.orderOut(nil)
        release(w)
    }

    func windowWillClose(_ notification: Notification) {
        if let w = window { release(w) }
    }

    private var vm: SlateVM?
    private func release(_ w: NSWindow) {
        w.contentView = nil                              // frees the web view
        vm?.pageGone()
    }

    private func make(_ state: AppState) -> NSWindow {
        vm = state.slate
        // A real window: normal traffic lights, resizable, full screen, in the Dock and Cmd-Tab. Not a utility panel.
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 860, height: 600),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                         backing: .buffered, defer: false)
        w.title = "Slate"
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.isReleasedWhenClosed = false
        w.collectionBehavior = [.fullScreenPrimary]
        w.backgroundColor = NSColor(red: 0.957, green: 0.945, blue: 0.918, alpha: 1)
        w.minSize = NSSize(width: 560, height: 380)
        w.delegate = self
        return w
    }

    private func place(_ w: NSWindow) {
        guard let v = NSScreen.main?.visibleFrame else { return }
        let size = w.frame.size
        w.setFrame(NSRect(x: v.midX - size.width / 2, y: v.midY - size.height / 2 + 30, width: size.width, height: size.height), display: true)
    }
}

/// The page itself (`Resources/slate/slate.html`), offline: KaTeX and Geist ship inside the app.
struct SlateWebView: NSViewRepresentable {
    let vm: SlateVM

    func makeNSView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(WeakHandler(context.coordinator), name: "slate")      // pills and the pointer
        let web = WKWebView(frame: .zero, configuration: cfg)
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = context.coordinator
        context.coordinator.vm = vm
        if let url = Bundle.main.url(forResource: "slate", withExtension: "html", subdirectory: "slate") {
            web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
        return web
    }

    func updateNSView(_ web: WKWebView, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator() }

    final class WeakHandler: NSObject, WKScriptMessageHandler {
        weak var target: Coordinator?
        init(_ t: Coordinator) { target = t }
        func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) { target?.receive(m.body) }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var vm: SlateVM?
        func receive(_ body: Any) {
            guard let d = body as? [String: Any] else { return }
            if let p = d["pill"] as? String { Task { @MainActor in self.vm?.pill(p) } }
            if let h = d["hover"] as? Bool { Task { @MainActor in self.vm?.hover(h) } }
            if d["hist"] != nil { Task { @MainActor in await self.vm?.loadHistory() } }
            if let id = d["open"] as? Int { let replay = d["replay"] as? Bool ?? false; Task { @MainActor in await self.vm?.openSaved(id, replay: replay) } }
        }
        func webView(_ web: WKWebView, didFinish navigation: WKNavigation!) {
            vm?.pageReady { [weak web] js in web?.evaluateJavaScript(js, completionHandler: nil) }
        }
    }
}
