import Foundation

/// What the engine's `setting` tool may change in the app (step B, card B6). The engine sends op `setting_apply` over the hands
/// wire; this applies it the same way the Settings tab and the menu bar do, then answers with the value it now HOLDS, so the
/// engine only says "done" when it is. Anything not in the list is refused here too.
@MainActor
public protocol SettingTarget: AnyObject {
    var speakReplies: Bool { get set }
    var voices: [String] { get }
    var voiceName: String { get }
    func setVoice(_ name: String) async
    /// Muting while she is mid-sentence stops her, as the menu bar button does.
    func stopSpeakingNow()
    /// F5: ambient mode as the app holds it now ("off" or "name"), and a request to change it (false when it could not: no consent, a class, a call).
    var ambientMode: String { get }
    func setAmbient(_ mode: String) async -> Bool
}

extension VoiceVM: SettingTarget {
    public func stopSpeakingNow() { if state == .speaking { end() } }
    public var ambientMode: String { ambient.rawValue }
    public func setAmbient(_ mode: String) async -> Bool {
        guard let m = AmbientMode(word: mode) else { return false }
        return await setAmbient(m)
    }
}

public enum SettingApply {
    @MainActor
    public static func run(name: String, value: JSONValue, on t: SettingTarget) async -> HandsResult {
        switch name {
        case "speak_replies":
            guard case .bool(let on) = value else { return .bad("BAD_VALUE") }
            t.speakReplies = on
            if !on { t.stopSpeakingNow() }
            return .good(["value": .bool(t.speakReplies)])
        case "voice":
            guard let want = value.stringValue else { return .bad("BAD_VALUE") }
            let voices = t.voices
            if want.lowercased() == "list" {
                return .good(["value": .string(t.voiceName), "voices": .array(voices.map { .string($0) })])
            }
            guard let match = voices.first(where: { $0.lowercased() == want.lowercased() }) else {
                return HandsResult(ok: false, data: .object(["error": .string("no such voice"),
                                                              "voices": .array(voices.map { .string($0) })]), code: nil)
            }
            await t.setVoice(match)
            return .good(["value": .string(t.voiceName)])
        case "ambient":
            guard let want = value.stringValue, let mode = AmbientMode(word: want) else { return .bad("BAD_VALUE") }
            _ = await t.setAmbient(mode.rawValue)
            return .good(["value": .string(t.ambientMode)])            // what it holds now: "off" if he said no to the consent, or a class or a call is on
        default:
            return .bad("NOT_ALLOWED")
        }
    }
}
