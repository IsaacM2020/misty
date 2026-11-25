import Foundation

public enum SetupStep: Int, CaseIterable, Sendable {
    case privacy, openrouter, fda, google, folders, chats, build
}

public struct SetupState: Equatable, Sendable {
    public var openrouterOk = false
    public var zdr = false
    public var fdaGranted = false
    public var skipChats = false
    public var googleEmail: String?
    public var googleSkipped = false
    public var estimateTotal: Double?
    public var capUsd: Double = 3.0
    public var estimateShown = false
    public init() {}
}

public enum SetupFlow {
    public static let allowedGoogle = ProcessInfo.processInfo.environment["MISTY_GOOGLE_ACCOUNT"] ?? "you@example.com"

    public static func canAdvance(from step: SetupStep, _ s: SetupState) -> Bool {
        switch step {
        case .privacy: return true
        case .openrouter: return s.openrouterOk && s.zdr
        case .fda: return s.fdaGranted || s.skipChats
        case .google: return s.googleEmail == allowedGoogle || s.googleSkipped
        case .folders: return true
        case .chats: return true
        case .build: return s.estimateShown && !overCap(s)
        }
    }

    public static func overCap(_ s: SetupState) -> Bool {
        guard let t = s.estimateTotal else { return false }
        return t > s.capUsd
    }

    public static func overCapMessage(_ s: SetupState) -> String? {
        guard overCap(s), let t = s.estimateTotal else { return nil }
        return String(format: "Estimated $%.2f is over your $%.0f cap", t, s.capUsd)
    }

    public static func next(_ step: SetupStep) -> SetupStep? { SetupStep(rawValue: step.rawValue + 1) }

    /// Polls `check` every `interval` seconds until it returns true (or the task is cancelled). Returns true on grant.
    public static func pollUntilGranted(interval: Double = 2, check: @Sendable () async -> Bool,
                                        sleep: @Sendable (Double) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }) async -> Bool {
        while !Task.isCancelled {
            if await check() { return true }
            do { try await sleep(interval) } catch { return false }
        }
        return false
    }
}
