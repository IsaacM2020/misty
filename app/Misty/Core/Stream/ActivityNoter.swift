import Foundation
import AppKit

/// Decides when the app tells the engine where he is (step C7): at once on a change, at most once every 5 seconds, plus a
/// heartbeat every 60 seconds while nothing changes, so the engine can tell a long stretch from the machine sleeping.
public struct ActivityGate: Sendable {
    public static let minGap: TimeInterval = 5
    public static let heartbeat: TimeInterval = 60
    private var lastKey = ""
    private var lastSent = Date.distantPast
    public init() {}

    public mutating func shouldSend(key: String, now: Date = Date()) -> Bool {
        let since = now.timeIntervalSince(lastSent)
        if key != lastKey {
            guard since >= Self.minGap else { return false }     // a flurry of switches: the next tick catches the last one
        } else if since < Self.heartbeat {
            return false
        }
        lastKey = key
        lastSent = now
        return true
    }
}

/// Watches his frontmost app and window and sends `activity.note`. Private apps send only their bundle id (so the engine
/// closes the stretch before) and never a title or document. No content is logged.
@MainActor
public final class ActivityNoter {
    private var gate = ActivityGate()
    private var timer: Timer?
    private let api: EngineAPI

    public init(api: EngineAPI) { self.api = api }

    public func start() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
    }

    private func tick() {
        let front = NSWorkspace.shared.frontmostApplication
        guard let front, front.bundleIdentifier != Bundle.main.bundleIdentifier, front.activationPolicy == .regular else { return }
        let bundle = front.bundleIdentifier ?? ""
        let pid = Int(front.processIdentifier)
        let name = front.localizedName ?? ""
        let isPrivate = ContextFrame.privateBundles.contains(bundle)
        Task.detached { [api] in
            let r = isPrivate ? nil : FocusReader.read(pid: pid)
            let key = "\(bundle)|\(r?.title ?? "")|\(r?.doc ?? "")"
            let send = await MainActor.run { self.gate.shouldSend(key: key) }
            guard send else { return }
            struct P: Encodable, Sendable { var bundle: String; var app: String; var title: String; var doc: String }
            let _: JSONValue? = try? await api.call("activity.note", P(bundle: bundle, app: isPrivate ? "" : name, title: r?.title ?? "", doc: r?.doc ?? ""))
        }
    }
}
