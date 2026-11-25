import AppKit
import SwiftUI

/// The "take notes?" card: a small floating window at the top right, just before an online class on the calendar.
/// Take notes starts an online class (and opens the class panel); Not this one closes it. It never starts on its own.
@MainActor
final class ClassAskPanel {
    static let shared = ClassAskPanel()
    private var window: NSPanel?

    func show(_ state: AppState) {
        let w = window ?? make(state)
        window = w
        place(w)
        w.orderFrontRegardless()
        Sound.shared.play(.done)
    }

    func hide() { window?.orderOut(nil) }

    private func make(_ state: AppState) -> NSPanel {
        let w = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 320, height: 132),
                        styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView, .utilityWindow],
                        backing: .buffered, defer: false)
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.isFloatingPanel = true
        w.level = .floating
        w.hidesOnDeactivate = false
        w.isReleasedWhenClosed = false
        w.isMovableByWindowBackground = true
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        w.contentView = NSHostingView(rootView: ClassAskView { [weak self] in self?.hide() }
            .environment(state))
        return w
    }

    private func place(_ w: NSPanel) {
        guard let v = NSScreen.main?.visibleFrame else { return }
        w.setFrameOrigin(NSPoint(x: v.maxX - w.frame.width - 12, y: v.maxY - w.frame.height - 12))
    }
}

private struct ClassAskView: View {
    @Environment(AppState.self) private var state
    let close: () -> Void

    var body: some View {
        let vm = state.classes
        VStack(alignment: .leading, spacing: 12) {
            if let a = vm.ask {
                Text(Self.line(a)).font(Theme.ui(17, 720)).fixedSize(horizontal: false, vertical: true)
                Text("Online: EDITH hears the Mac's sound as the teacher and your mic as you.")
                    .font(Theme.small).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    GoButton("Take notes") {
                        close()
                        Task { await vm.takeNotes() }
                    }
                    GoButton("Not this one", filled: false) {
                        vm.skipAsk()
                        close()
                    }
                }
            }
        }
        .padding(.horizontal, 16).padding(.top, 22).padding(.bottom, 14)
        .frame(width: 320, alignment: .leading)
        .background(Theme.bg)
        .onChange(of: vm.ask == nil) { _, gone in if gone { close() } }   // a class started some other way
    }

    static func line(_ a: ClassVM.Ask) -> String {
        if a.detected { return "\(a.title) detected. Take notes?" }
        let mins = Int((a.startsIn / 60).rounded())
        let when = a.startsIn <= 30 ? (a.startsIn < -60 ? "started \(-mins) min ago" : "is starting") : "starts in \(max(1, mins)) min"
        return "\(a.title) \(when). Take notes?"
    }
}
