import Foundation
import Observation
#if canImport(AppKit)
import AppKit
import ApplicationServices
import CoreServices
import AVFoundation
#endif

/// Every macOS permission Misty needs, in one place, so Isaac grants them once on his own time instead of meeting a
/// popup in the middle of a task. Automation ("Misty wants to control Safari") pops up right here; the rest live in
/// System Settings, which macOS never lets an app flip for itself, so their button opens the exact pane.
public enum PermState: String, Sendable { case granted, notYet, denied }

public struct PermItem: Identifiable, Sendable, Equatable {
    public enum Kind: Sendable, Equatable { case accessibility, screen, input, fullDisk, microphone, automation(bundle: String) }
    public let id: String
    public let name: String
    public let why: String
    public let kind: Kind
    public var state: PermState
}

public enum Perms {
    static let pane = "x-apple.systempreferences:com.apple.preference.security?"

    /// What `AEDeterminePermissionToAutomateTarget` returned, as a state. -1744: macOS would have to ask;
    /// -600: the app isn't running, so nothing is known yet.
    public static func automationState(_ code: Int32) -> PermState {
        switch code {
        case 0: return .granted
        case -1743: return .denied
        default: return .notYet
        }
    }

    static func paneURL(_ k: PermItem.Kind) -> URL? {
        let name: String
        switch k {
        case .accessibility: name = "Privacy_Accessibility"
        case .screen: name = "Privacy_ScreenCapture"
        case .input: name = "Privacy_ListenEvent"
        case .fullDisk: name = "Privacy_AllFiles"
        case .microphone: name = "Privacy_Microphone"
        case .automation: name = "Privacy_Automation"
        }
        return URL(string: pane + name)
    }

    /// Browsers and helpers Misty reads "this" from. Only the ones installed on this Mac are listed.
    static let automationTargets: [(bundle: String, name: String, why: String)] = [
        ("com.apple.Safari", "Safari", "Read the tab you're on when you say \u{201C}this page\u{201D}"),
        ("com.google.Chrome", "Chrome", "Read the tab you're on when you say \u{201C}this page\u{201D}"),
        ("com.brave.Browser", "Brave", "Read the tab you're on when you say \u{201C}this page\u{201D}"),
        ("com.apple.finder", "Finder", "See the file you selected when you say \u{201C}this file\u{201D}"),
        ("com.apple.systemevents", "System Events", "See which document is open in other apps"),
    ]
}

#if canImport(AppKit)
@MainActor
@Observable
public final class PermissionsVM {
    public private(set) var items: [PermItem] = []
    public private(set) var working: String?
    private var stuckUntil: [String: Date] = [:]

    public init() {}

    public var missing: Int { items.filter { $0.state != .granted }.count }

    public func refresh() async {
        var out: [PermItem] = [
            PermItem(id: "accessibility", name: "Accessibility", why: "Look at apps and press buttons for you",
                     kind: .accessibility, state: AXIsProcessTrusted() ? .granted : .notYet),
            PermItem(id: "screen", name: "Screen Recording", why: "See the screen when a task needs a look",
                     kind: .screen, state: CGPreflightScreenCaptureAccess() ? .granted : .notYet),
            PermItem(id: "input", name: "Input Monitoring", why: "Notice when you grab the mouse, so EDITH backs off",
                     kind: .input, state: CGPreflightListenEventAccess() ? .granted : .notYet),
            PermItem(id: "fulldisk", name: "Full Disk Access", why: "Read iMessage and your folders for memory",
                     kind: .fullDisk, state: Self.hasFullDisk() ? .granted : .notYet),
            PermItem(id: "mic", name: "Microphone", why: "Hear you after you tap Command+Option",
                     kind: .microphone, state: Self.micState()),
        ]
        // Show the list now: one app that never answers the check below (Chrome did, 16 Nov) used to hide the whole section.
        let known = items
        for t in Perms.automationTargets where NSWorkspace.shared.urlForApplication(withBundleIdentifier: t.bundle) != nil {
            out.append(PermItem(id: t.bundle, name: "Control \(t.name)", why: t.why, kind: .automation(bundle: t.bundle),
                                state: known.first { $0.id == t.bundle }?.state ?? .notYet))
        }
        items = out
        for (i, item) in out.enumerated() {
            guard case .automation(let bundle) = item.kind else { continue }
            if let until = stuckUntil[bundle], until > Date() { continue }  // it hung recently: don't pile up threads
            guard let code = await Self.automation(bundle, ask: false, timeout: 2) else {
                stuckUntil[bundle] = Date().addingTimeInterval(60)
                continue                                                   // no answer in 2 s: keep what we last knew
            }
            stuckUntil[bundle] = nil
            if code == -600 { continue }                                   // not running: keep what we last knew
            out[i].state = Perms.automationState(code)
        }
        items = out
    }

    /// One permission: pop the macOS question where macOS allows it, otherwise open the right Settings pane.
    public func ask(_ item: PermItem) async {
        working = item.id
        defer { working = nil }
        switch item.kind {
        case .automation(let bundle):
            if item.state == .denied { open(item.kind); break }
            await Self.launchHidden(bundle)
            _ = await Self.automation(bundle, ask: true, timeout: 120)      // he may take a while to answer the popup
        case .accessibility:
            let opts = ["AXTrustedCheckOptionPrompt": true] as CFDictionary      // kAXTrustedCheckOptionPrompt, by value (Swift 6)
            if !AXIsProcessTrustedWithOptions(opts) { open(item.kind) }
        case .screen:
            if !CGRequestScreenCaptureAccess() { open(item.kind) }
        case .input:
            if !CGRequestListenEventAccess() { open(item.kind) }
        case .fullDisk:
            open(item.kind)
        case .microphone:
            let ok = item.state == .denied ? false : await AudioIO.askMic()
            if !ok { open(item.kind) }
        }
        await refresh()
    }

    /// The in-place popups first (one after another), then the first Settings pane still missing.
    public func setUpAll() async {
        await refresh()
        for item in items where item.state == .notYet {
            if case .automation = item.kind { await ask(item) }
        }
        if let pane = items.first(where: { $0.state != .granted && !$0.isAutomation }) { await ask(pane) }
        await refresh()
    }

    public func open(_ k: PermItem.Kind) {
        if let u = Perms.paneURL(k) { NSWorkspace.shared.open(u) }
    }

    static func micState() -> PermState {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return .granted
        case .notDetermined: return .notYet
        default: return .denied
        }
    }

    /// Full Disk Access has no check call: try reading a file only it unlocks.
    nonisolated static func hasFullDisk() -> Bool {
        let home = NSHomeDirectory()
        if FileManager.default.fileExists(atPath: home + "/Library/Messages/chat.db") {
            return FileHandle(forReadingAtPath: home + "/Library/Messages/chat.db") != nil
        }
        return (try? FileManager.default.contentsOfDirectory(atPath: home + "/Library/Safari")) != nil
    }

    /// Blocks while macOS shows its popup, and can block forever when the target app is stuck (Chrome, 16 Nov), so it runs
    /// on its own thread and gives up after `timeout` seconds (nil = no answer). The thread itself can't be stopped.
    nonisolated static func automation(_ bundle: String, ask: Bool, timeout: Double) async -> Int32? {
        await withCheckedContinuation { (c: CheckedContinuation<Int32?, Never>) in
            let once = Once(c)
            Thread.detachNewThread { once.resume(automationNow(bundle, ask: ask)) }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { once.resume(nil) }
        }
    }

    nonisolated static func automationNow(_ bundle: String, ask: Bool) -> Int32 {
        var desc = AEAddressDesc()
        let id = Array(bundle.utf8)
        let made = id.withUnsafeBytes { AECreateDesc(DescType(typeApplicationBundleID), $0.baseAddress, id.count, &desc) }
        guard made == noErr else { return Int32(made) }
        defer { AEDisposeDesc(&desc) }
        return AEDeterminePermissionToAutomateTarget(&desc, AEEventClass(typeWildCard), AEEventID(typeWildCard), ask)
    }

    /// macOS only asks about an app that's running; start it in the background without stealing focus.
    static func launchHidden(_ bundle: String) async {
        if !NSRunningApplication.runningApplications(withBundleIdentifier: bundle).isEmpty { return }
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else { return }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = false
        cfg.hides = true
        _ = try? await NSWorkspace.shared.openApplication(at: url, configuration: cfg)
        for _ in 0..<20 where NSRunningApplication.runningApplications(withBundleIdentifier: bundle).isEmpty {
            try? await Task.sleep(for: .milliseconds(150))
        }
    }
}

/// Resumes a continuation exactly once: the first of "the check answered" and "time is up" wins.
final class Once: @unchecked Sendable {
    private let lock = NSLock()
    private var c: CheckedContinuation<Int32?, Never>?
    init(_ c: CheckedContinuation<Int32?, Never>) { self.c = c }
    func resume(_ v: Int32?) {
        lock.lock()
        let k = c
        c = nil
        lock.unlock()
        k?.resume(returning: v)
    }
}

extension PermItem {
    var isAutomation: Bool { if case .automation = kind { return true } else { return false } }
}
#endif
