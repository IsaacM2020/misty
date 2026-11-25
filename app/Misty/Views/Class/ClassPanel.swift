import AppKit
import SwiftUI

/// The class window: a normal Mac window (close, minimise, full screen), opened on the right edge next to Zoom.
/// It was a floating utility panel with only a tiny close button; Isaac wanted it to act like every other window.
@MainActor
final class ClassPanel {
    static let shared = ClassPanel()
    private var window: NSWindow?

    func show(_ state: AppState) {
        let w = window ?? make(state)
        window = w
        if w.isMiniaturized { w.deminiaturize(nil) }
        else if !w.isVisible { place(w) }
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func hide() { window?.orderOut(nil) }

    private func make(_ state: AppState) -> NSWindow {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 380, height: 640),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                         backing: .buffered, defer: false)
        w.title = "Class"
        w.titlebarAppearsTransparent = true
        w.isReleasedWhenClosed = false
        w.collectionBehavior = [.fullScreenPrimary]      // the green button takes it full screen
        w.minSize = NSSize(width: 320, height: 160)
        w.contentView = NSHostingView(rootView: ClassPanelView().environment(state))
        return w
    }

    /// Right edge of the screen, full height minus a margin: next to whatever he has open.
    private func place(_ w: NSWindow) {
        guard let v = NSScreen.main?.visibleFrame else { return }
        w.setFrame(NSRect(x: v.maxX - 380 - 12, y: v.minY + 12, width: 380, height: v.height - 24), display: true)
    }
}
