import AppKit

/// The app Isaac was using before he switched to Misty, so "this doc" means what he had open, not Misty's own window.
@MainActor
final class LastApp {
    static let shared = LastApp()
    private var last: NSRunningApplication?
    private var token: NSObjectProtocol?

    func start() {
        guard token == nil else { return }
        remember(NSWorkspace.shared.frontmostApplication)
        token = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { note in
            let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            MainActor.assumeIsolated { LastApp.shared.remember(app) }
        }
    }

    private func remember(_ app: NSRunningApplication?) {
        guard let app, app.bundleIdentifier != Bundle.main.bundleIdentifier, app.activationPolicy == .regular else { return }
        last = app
    }

    /// The frontmost app if it isn't Misty, else the one before it.
    func current() -> (bundleId: String, pid: Int, name: String)? {
        let front = NSWorkspace.shared.frontmostApplication
        let mine = front?.bundleIdentifier == Bundle.main.bundleIdentifier
        let app = (!mine && front?.activationPolicy == .regular ? front : nil) ?? last   // skip Misty and system popups
        guard let app, !app.isTerminated else { return nil }
        return (app.bundleIdentifier ?? "", Int(app.processIdentifier), app.localizedName ?? "")
    }
}
