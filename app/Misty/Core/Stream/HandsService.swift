import Foundation
import AppKit

public struct HandsResult: Equatable, Sendable {
    public var ok: Bool
    public var data: JSONValue
    public var code: String?
    static func good(_ data: [String: JSONValue] = [:]) -> HandsResult { HandsResult(ok: true, data: .object(data), code: nil) }
    static func bad(_ code: String) -> HandsResult { HandsResult(ok: false, data: .null, code: code) }
}

/// Runs one `hands.request` op against an AXProvider. Permissions are checked first, so a missing grant comes back as a
/// plain code (`NOT_TRUSTED`, `SCREEN_RECORDING_DENIED`) instead of an event that silently goes nowhere.
public struct HandsService: Sendable {
    let provider: AXProvider
    public init(provider: AXProvider) { self.provider = provider }

    private enum Needs { case none, accessibility, screen, input }

    private static func needs(_ op: String) -> Needs? {
        switch op {
        case "permissions", "front_app", "last_app", "open_app", "hide_apps", "screen_frame", "cursor", "private_apps": return Needs.none
        case "ax_tree", "ax_snapshot", "ax_find", "ax_perform", "ax_set_value", "window_frames", "set_window_frame",
             "menu_list", "menu_press", "window_list", "set_minimised", "context_frame", "read_window_text": return .accessibility
        case "capture_window", "ocr_window": return .screen
        case "pointer", "keys", "type_text", "probe_post_to_pid": return .input
        default: return nil
        }
    }

    public func handle(op: String, args: JSONValue) async -> HandsResult {
        guard let need = Self.needs(op) else { return .bad("UNKNOWN_OP") }
        let perms = provider.permissions()
        switch need {
        case .accessibility where perms["accessibility"] != true: return .bad("NOT_TRUSTED")
        case .screen where perms["screen_recording"] != true: return .bad("SCREEN_RECORDING_DENIED")
        case .input where perms["post_events"] != true: return .bad("NOT_TRUSTED")
        default: break
        }
        do { return try await run(op, args, perms) }
        catch HandsError.code(let c) { return .bad(c) }
        catch { return .bad("HANDS_FAILED") }
    }

    /// Never Misty herself: reading her own window's accessibility tree from a background thread crashes SwiftUI
    /// (24 Oct, a voice turn sent from the Chat screen), and she has no business clicking her own buttons.
    private func pid(_ a: JSONValue) throws -> Int {
        guard let v = a["pid"]?.intValue else { throw HandsError.code("BAD_ARGS") }
        guard v != Int(getpid()) else { throw HandsError.code("IS_MISTY") }
        return v
    }
    private func str(_ a: JSONValue, _ k: String) throws -> String { guard let v = a[k]?.stringValue else { throw HandsError.code("BAD_ARGS") }; return v }
    private func num(_ a: JSONValue, _ k: String) throws -> Double {
        guard case .number(let v)? = a[k] else { throw HandsError.code("BAD_ARGS") }
        return v
    }
    private func frame(_ a: JSONValue) throws -> [Double] {
        guard case .array(let xs)? = a["frame"], xs.count == 4 else { throw HandsError.code("BAD_ARGS") }
        return try xs.map { guard case .number(let v) = $0 else { throw HandsError.code("BAD_ARGS") }; return v }
    }
    private func strings(_ a: JSONValue, _ k: String) throws -> [String] {
        guard case .array(let xs)? = a[k] else { throw HandsError.code("BAD_ARGS") }
        return try xs.map { guard case .string(let v) = $0 else { throw HandsError.code("BAD_ARGS") }; return v }
    }
    private func snapJSON(_ n: SnapNode) -> JSONValue {
        var o: [String: JSONValue] = [
            "ref": .string(n.ref), "role": .string(n.role), "label": .string(n.label), "value": .string(n.value),
            "frame": nums(n.frame), "secure": .bool(n.secure), "actions": .array(n.actions.map { .string($0) }),
            "depth": .number(Double(n.depth)), "focused": .bool(n.focused), "selected": .bool(n.selected),
            "region": .string(n.region), "rows": .number(Double(n.rows)), "path": .array(n.path.map { .string($0) })]
        if !n.subrole.isEmpty { o["subrole"] = .string(n.subrole) }
        if !n.desc.isEmpty { o["desc"] = .string(n.desc) }
        if !n.help.isEmpty { o["help"] = .string(n.help) }
        if !n.ident.isEmpty { o["ident"] = .string(n.ident) }
        return .object(o)
    }
    private func nums(_ f: [Double]) -> JSONValue { .array(f.map { .number($0) }) }
    private func appJSON(_ i: HandsAppInfo) -> HandsResult {
        .good(["bundle_id": .string(i.bundleId), "pid": .number(Double(i.pid)), "name": .string(i.name)])
    }

    private func run(_ op: String, _ a: JSONValue, _ perms: [String: Bool]) async throws -> HandsResult {
        switch op {
        case "permissions":
            return .good(Dictionary(uniqueKeysWithValues: perms.map { ($0.key, JSONValue.bool($0.value)) }))
        case "front_app": return appJSON(try provider.frontApp())
        case "open_app": return appJSON(try await provider.openApp(name: try str(a, "name")))
        case "hide_apps":
            let r = try provider.hideApps(except: a["except"] == nil ? [] : try strings(a, "except"))
            return .good(["hidden": .array(r.hidden.map { .string($0) }), "visible": .array(r.visible.map { .string($0) })])
        case "ax_tree":
            guard let max = a["max_nodes"]?.intValue else { throw HandsError.code("BAD_ARGS") }
            let nodes = try provider.axTree(pid: try pid(a), maxNodes: max)
            return .good(["nodes": .array(nodes.map { n in .object([
                "ref": .string(n.ref), "role": .string(n.role), "label": .string(n.label), "value": .string(n.value),
                "frame": nums(n.frame), "secure": .bool(n.secure), "actions": .array(n.actions.map { .string($0) }),
                "depth": .number(Double(n.depth)), "focused": .bool(n.focused)]) })])
        case "ax_snapshot":
            guard let max = a["max_nodes"]?.intValue else { throw HandsError.code("BAD_ARGS") }
            let preview = a["list_preview"]?.intValue ?? 5
            return .good(["nodes": .array(try provider.axSnapshot(pid: try pid(a), maxNodes: max, listPreview: preview).map(snapJSON))])
        case "ax_find":
            let limit = a["limit"]?.intValue ?? 8
            return .good(["nodes": .array(try provider.axFind(pid: try pid(a), query: try str(a, "query"), limit: limit).map(snapJSON))])
        case "menu_list":
            let items = try provider.menuList(pid: try pid(a), path: try strings(a, "path"))
            return .good(["items": .array(items.map { .object(["title": .string($0.title), "shortcut": .string($0.shortcut),
                                                                "enabled": .bool($0.enabled), "marked": .bool($0.marked)]) })])
        case "menu_press":
            return .good(["done": .bool(try provider.menuPress(pid: try pid(a), path: try strings(a, "path")))])
        case "window_list":
            return .good(["windows": .array(try provider.windowList().map { w in .object([
                "pid": .number(Double(w.pid)), "bundle_id": .string(w.bundleId), "app": .string(w.app),
                "index": .number(Double(w.index)), "title": .string(w.title), "frame": nums(w.frame),
                "minimised": .bool(w.minimised), "fullscreen": .bool(w.fullscreen)]) })])
        case "set_minimised":
            guard let index = a["index"]?.intValue, case .bool(let flag)? = a["minimised"] else { throw HandsError.code("BAD_ARGS") }
            return .good(["done": .bool(try provider.setMinimised(pid: try pid(a), index: index, minimised: flag))])
        case "ax_perform":
            return .good(["done": .bool(try provider.axPerform(pid: try pid(a), ref: try str(a, "ref"), action: try str(a, "action")))])
        case "ax_set_value":
            return .good(["done": .bool(try provider.axSetValue(pid: try pid(a), ref: try str(a, "ref"), text: try str(a, "text")))])
        case "private_apps":
            // The engine's list of apps she must not look at. Read back so the engine can say "verified".
            guard case .array(let xs)? = a["bundles"] else { throw HandsError.code("BAD_ARGS") }
            let ids = xs.compactMap { $0.stringValue }
            ContextFrame.privateBundles = Set(ids)
            return .good(["bundles": .array(ContextFrame.privateBundles.sorted().map { .string($0) })])
        case "context_frame":
            // His last app (never Misty), as one small JSON frame. Counts and timing are logged by the caller, never content.
            guard let i = await MainActor.run(body: { LastApp.shared.current() }) else { throw HandsError.code("NO_FRONT_APP") }
            let info = HandsAppInfo(bundleId: i.bundleId, pid: i.pid, name: i.name)
            if ContextFrame.privateBundles.contains(info.bundleId) { return .good(ContextFrame.make(app: info, reading: nil).json.objectValue ?? [:]) }
            let reading = try? provider.focusReading(pid: i.pid)
            return .good(ContextFrame.make(app: info, reading: reading).json.objectValue ?? [:])
        case "read_window_text", "ocr_window":
            // His last app, never Misty. Private apps are refused here, so no engine path can read them.
            guard let i = await MainActor.run(body: { LastApp.shared.current() }) else { throw HandsError.code("NO_FRONT_APP") }
            if ContextFrame.privateBundles.contains(i.bundleId) { return .good(["private": .bool(true)]) }
            var head: [String: JSONValue] = ["app": .string(i.name), "bundle_id": .string(i.bundleId)]
            if op == "ocr_window" {
                let t = ScreenText.capped(try await provider.ocrWindow(pid: i.pid))
                head["text"] = .string(t.text); head["cut"] = .bool(t.cut)
                return .good(head)
            }
            let r = try provider.windowText(pid: i.pid)
            let t = ScreenText.capped(ScreenText.marked(r.text, selection: r.selection))
            head["title"] = .string((try? provider.focusReading(pid: i.pid))?.title ?? "")
            head["text"] = .string(t.text); head["cut"] = .bool(t.cut)
            return .good(head)
        case "capture_window":
            let owner = NSRunningApplication(processIdentifier: pid_t(try pid(a)))?.bundleIdentifier ?? ""
            if ContextFrame.privateBundles.contains(owner) { throw HandsError.code("PRIVATE_APP") }       // look refuses private apps too
            return .good(["png": .string(try await provider.captureWindow(pid: try pid(a)).base64EncodedString())])
        case "pointer":
            var restore = false
            if case .bool(let b)? = a["restore"] { restore = b }
            try provider.pointer(x: try num(a, "x"), y: try num(a, "y"), kind: try str(a, "kind"), restore: restore)
            return .good()
        case "probe_post_to_pid":
            let moved = try provider.probePostToPid(pid: try pid(a), x: try num(a, "x"), y: try num(a, "y"))
            return .good(["pointer_moved": .bool(moved)])
        case "keys": try provider.keys(combo: try str(a, "combo")); return .good()
        case "type_text": try provider.typeText(try str(a, "text")); return .good()
        case "window_frames": return .good(["frames": .array(try provider.windowFrames(pid: try pid(a)).map(nums))])
        case "set_window_frame":
            guard let index = a["index"]?.intValue else { throw HandsError.code("BAD_ARGS") }
            var animate = false
            if case .bool(let b)? = a["animate"] { animate = b }
            return .good(["done": .bool(try provider.setWindowFrame(pid: try pid(a), index: index, frame: try frame(a), animate: animate))])
        case "screen_frame": return .good(["frame": nums(try provider.screenFrame())])
        case "last_app":
            guard let i = await MainActor.run(body: { LastApp.shared.current() }) else { throw HandsError.code("NO_FRONT_APP") }
            return .good(["bundle_id": .string(i.bundleId), "pid": .number(Double(i.pid)), "name": .string(i.name)])
        case "cursor":
            let what = try str(a, "do"), label = a["label"]?.stringValue ?? "", f = try? frame(a)
            let secs = await MainActor.run { StreamCursor.shared.handle(what, frame: f, label: label) }
            return .good(["secs": .number(secs)])
        default: throw HandsError.code("UNKNOWN_OP")
        }
    }
}

/// The engine asks, this answers: runs the op and sends `hands.result`. Also registers the app as the engine's hands and
/// forwards the person's own keys and mouse moves (so a run can notice a take-over).
public final class HandsBridge: Sendable {
    private let api: EngineAPI
    private let service: HandsService

    public init(api: EngineAPI, service: HandsService) { self.api = api; self.service = service }

    private struct ResultParams: Encodable, Sendable {
        var reqId: String
        var ok: Bool
        var data: JSONValue
        var code: String?
    }
    private struct InputParams: Encodable, Sendable { var ts: Double }

    public func register() async {
        let _: JSONValue? = try? await api.call("hands.register", Empty())
    }

    public func handle(reqId: String, op: String, args: JSONValue) async {
        let r = await service.handle(op: op, args: args)
        let _: JSONValue? = try? await api.call("hands.result", ResultParams(reqId: reqId, ok: r.ok, data: r.data, code: r.code))
    }

    /// An answer to a request the app handles itself (a setting the engine asked it to change).
    public func reply(reqId: String, _ r: HandsResult) async {
        let _: JSONValue? = try? await api.call("hands.result", ResultParams(reqId: reqId, ok: r.ok, data: r.data, code: r.code))
    }

    public func userInput(ts: Double) async {
        let _: JSONValue? = try? await api.call("hands.user_input", InputParams(ts: ts))
    }
}
