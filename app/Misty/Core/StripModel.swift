import Foundation

public struct Banner: Equatable, Sendable, Identifiable {
    public enum Action: Equatable, Sendable { case openFullDiskAccess, reconnectGoogle, exportWhatsApp, raiseCap, retryFailed, openLogs, none }
    public enum Severity: Equatable, Sendable { case info, warning, error }
    public var id: String
    public var text: String
    public var severity: Severity
    public var action: Action
    public var actionTitle: String?
}

public struct StripModel: Equatable, Sendable {
    public enum Dot: Equatable, Sendable { case green, amber, red }
    public var dot: Dot
    public var text: String
    public var spend: String
    public var build: String?
    public var banners: [Banner]

    static func money(_ v: Double) -> String { String(format: "$%.2f", v) }

    /// Small caps (a cent a day for judging) need the extra digits; whole-dollar ones do not.
    static func money2(_ v: Double) -> String { v < 0.1 ? String(format: "$%.3f", v) : String(format: "$%.2f", v) }

    static func capName(_ group: String) -> String {
        ["judge": "Judge", "summarise": "Summary", "ask": "Ask", "core": "Core", "act": "Act"][group] ?? group.capitalized
    }

    /// "judge cap" for the sidebar when every waiting job is held by one cap, "caps" when by several.
    static func capReason(_ s: Status) -> String? {
        guard let c = s.capped, !c.isEmpty else { return "cap" }
        if c.count == 1 { return c[0].group == "daily" ? "daily cap" : (c[0].group == "build" ? "build budget" : "\(c[0].group) cap") }
        return "caps"
    }

    static func grouped(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = Locale(identifier: "en_US")
        return f.string(from: NSNumber(value: n)) ?? String(n)
    }

    static let phaseLabel: [String: String] = [
        "discover": "Finding", "prep": "Reading", "worth": "Triaging", "compile": "Compiling", "embed": "Linking", "resolve": "Sorting names",
    ]

    public static func make(status: Status?, engine: EngineState) -> StripModel {
        guard let s = status else {
            let banners = engine == .crashLoop
                ? [Banner(id: "crash", text: "Engine keeps stopping: open logs", severity: .error, action: .openLogs, actionTitle: "Open logs")]
                : []
            return StripModel(dot: engine == .running ? .amber : .red, text: engine == .running ? "Connecting" : "Engine off",
                              spend: "", build: nil, banners: banners)
        }
        var banners: [Banner] = []
        if engine == .crashLoop {
            banners.append(Banner(id: "crash", text: "Engine keeps stopping: open logs", severity: .error,
                                  action: .openLogs, actionTitle: "Open logs"))
        }
        if !s.online {
            banners.append(Banner(id: "offline", text: "Offline: queued, will resume", severity: .error, action: .none, actionTitle: nil))
        }
        for src in s.sources {
            // one banner per problem, not per source: Mail and Calendar share the Google sign-in, Messages and
            // WhatsApp share Full Disk Access
            switch src.health {
            case "needs_fda":
                guard !banners.contains(where: { $0.action == .openFullDiskAccess }) else { break }
                banners.append(Banner(id: "fda-\(src.source)", text: "EDITH needs Full Disk Access", severity: .warning,
                                      action: .openFullDiskAccess, actionTitle: "Open settings"))
            case "needs_auth":
                guard !banners.contains(where: { $0.action == .reconnectGoogle }) else { break }
                banners.append(Banner(id: "auth-\(src.source)", text: "Reconnect Google", severity: .warning,
                                      action: .reconnectGoogle, actionTitle: "Reconnect"))
            case "needs_export":
                banners.append(Banner(id: "export-\(src.source)", text: "WhatsApp changed: export a chat", severity: .warning,
                                      action: .exportWhatsApp, actionTitle: "How"))
            default: break
            }
        }
        let capped = s.queue["waiting_cap"] ?? 0
        if capped > 0 {
            // while the first build runs, it is the build budget that ran out, not today's: say that one
            if let b = s.spend.build, s.build != nil {
                banners.append(Banner(id: "cap", text: "Build budget used: \(money(b)) of \(money(s.spend.capBuild)). Raise it to keep going.",
                                      severity: .warning, action: .raiseCap, actionTitle: "Raise cap"))
            } else {
                // say WHICH cap: a purpose sub-cap (judge) is not the day's money, and Raise cap would not help it
                let causes = (s.capped ?? []).sorted { $0.waiting > $1.waiting }
                if causes.isEmpty {
                    banners.append(Banner(id: "cap", text: "Cap hit: \(money(s.spend.today)) spent today. Raise it or wait until midnight.",
                                          severity: .warning, action: .raiseCap, actionTitle: "Raise cap"))
                }
                for c in causes {
                    let daily = c.group == "daily"
                    let name = daily ? "Daily cap" : "\(capName(c.group)) cap"
                    let tail = daily ? "Raise it or wait until midnight." : "They restart at midnight, or when it is raised."
                    banners.append(Banner(id: "cap-\(c.group)",
                                          text: "\(name) hit: \(money2(c.spent)) of \(money2(c.cap)) today, \(grouped(c.waiting)) waiting. \(tail)",
                                          severity: .warning, action: daily ? .raiseCap : .none, actionTitle: daily ? "Raise cap" : nil))
                }
            }
        }
        let failed = (s.queue["failed"] ?? 0)
        if failed > 0 {
            banners.append(Banner(id: "failed", text: "\(failed) failed", severity: .warning, action: .retryFailed,
                                  actionTitle: "Retry"))
        }
        let queued = ["queued", "running", "waiting_network", "waiting_cap", "waiting_quota"]
            .reduce(0) { $0 + (s.queue[$1] ?? 0) }
        let dot: Dot = (!s.online || engine == .crashLoop) ? .red
            : (s.paused || capped > 0 || failed > 0 || engine != .running) ? .amber : .green
        let spend: String
        if let b = s.spend.build, s.build != nil {
            spend = "\(money(b)) / \(money(s.spend.capBuild))"
        } else {
            spend = "\(money(s.spend.today)) / \(money(s.spend.capDaily))"
        }
        var buildText: String?
        if let b = s.build, let label = phaseLabel[b.phase] {
            buildText = "\(label) \(grouped(b.done)) / \(grouped(b.total))"
        }
        // paused: the background work isn't stuck, it's waiting for Isaac to resume it
        let text: String
        if s.paused { text = "Paused · \(grouped(queued)) waiting" }
        else if capped > 0, let why = capReason(s) {
            let rest = queued - capped
            text = (rest > 0 ? "\(grouped(rest)) queued, " : "") + "\(grouped(capped)) waiting: \(why)"
        } else { text = "\(grouped(queued)) queued" }
        return StripModel(dot: dot, text: text, spend: spend, build: buildText, banners: banners)
    }
}
