import Foundation
import CoreGraphics

/// Keys and mouse for the engine (CGEvent). Every event Misty posts carries `Input.tag` in the event's user-data field,
/// so the take-over tap can tell her own input from the person's.
public enum Input {
    /// A per-launch random 32-bit value.
    public static let tag: Int64 = Int64(UInt32.random(in: 1...UInt32.max))

    public struct Combo: Equatable, Sendable {
        public var code: CGKeyCode
        public var flags: CGEventFlags
    }

    private static let codes: [String: CGKeyCode] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
        "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
        "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
        "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
        "enter": 36, "return": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
        "left": 123, "right": 124, "down": 125, "up": 126, "arrowleft": 123, "arrowright": 124, "arrowdown": 125,
        "arrowup": 126, "pageup": 116, "pagedown": 121, "home": 115, "end": 119,
    ]
    private static let mods: [String: CGEventFlags] = [
        "meta": .maskCommand, "cmd": .maskCommand, "command": .maskCommand, "control": .maskControl,
        "ctrl": .maskControl, "alt": .maskAlternate, "option": .maskAlternate, "shift": .maskShift,
    ]

    /// "Meta+L", "Shift+Tab", "Enter", "PageDown". Nil when a part is unknown.
    public static func parse(_ combo: String) -> Combo? {
        let parts = combo.split(separator: "+", omittingEmptySubsequences: true).map { String($0).lowercased() }
        guard let key = parts.last, let code = codes[key] else { return nil }
        var flags = CGEventFlags()
        for m in parts.dropLast() {
            guard let f = mods[m] else { return nil }
            flags.insert(f)
        }
        return Combo(code: code, flags: flags)
    }

    private static func post(_ e: CGEvent?) {
        guard let e else { return }
        e.setIntegerValueField(.eventSourceUserData, value: tag)
        e.post(tap: .cghidEventTap)
    }

    public static func sendKeys(_ combo: String) throws {
        guard let c = parse(combo) else { throw HandsError.code("BAD_ARGS") }
        let src = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let e = CGEvent(keyboardEventSource: src, virtualKey: c.code, keyDown: down)
            e?.flags = c.flags
            post(e)
        }
    }

    public static func typeText(_ text: String) {
        let src = CGEventSource(stateID: .hidSystemState)
        let units = Array(text.utf16)
        var i = 0
        while i < units.count {
            let chunk = Array(units[i..<min(i + 16, units.count)])
            for down in [true, false] {
                let e = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: down)
                e?.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: chunk)
                post(e)
            }
            i += chunk.count
        }
    }

    /// Debug probe for `hands.probe_post_to_pid`: a left click aimed at one process, not at the screen. Returns whether the
    /// visible pointer moved (it should not). Whether the click landed is checked by the caller reading the app's own state.
    public static func probePostToPid(pid: Int, x: Double, y: Double) -> Bool {
        let before = CGEvent(source: nil)?.location ?? .zero
        let src = CGEventSource(stateID: .hidSystemState)
        let pt = CGPoint(x: x, y: y)
        for t in [CGEventType.leftMouseDown, .leftMouseUp] {
            let e = CGEvent(mouseEventSource: src, mouseType: t, mouseCursorPosition: pt, mouseButton: .left)
            e?.setIntegerValueField(.mouseEventClickState, value: 1)
            e?.setIntegerValueField(.eventSourceUserData, value: tag)
            e?.postToPid(pid_t(pid))
        }
        usleep(100_000)
        let after = CGEvent(source: nil)?.location ?? .zero
        return abs(after.x - before.x) > 0.5 || abs(after.y - before.y) > 0.5
    }

    public static func click(x: Double, y: Double, kind: String, restore: Bool = false) throws {
        guard ["click", "double", "right"].contains(kind) else { throw HandsError.code("BAD_ARGS") }
        let src = CGEventSource(stateID: .hidSystemState)
        let before = restore ? CGEvent(source: nil)?.location : nil
        let pt = CGPoint(x: x, y: y)
        post(CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: pt, mouseButton: .left))
        let right = kind == "right"
        let (downType, upType, button): (CGEventType, CGEventType, CGMouseButton) =
            right ? (.rightMouseDown, .rightMouseUp, .right) : (.leftMouseDown, .leftMouseUp, .left)
        for n in 1...(kind == "double" ? 2 : 1) {
            for t in [downType, upType] {
                let e = CGEvent(mouseEventSource: src, mouseType: t, mouseCursorPosition: pt, mouseButton: button)
                e?.setIntegerValueField(.mouseEventClickState, value: Int64(n))
                post(e)
            }
        }
        if let before {                                   // the pointer is the person's: put it back where it was
            usleep(40_000)
            CGWarpMouseCursorPosition(before)
            CGAssociateMouseAndMouseCursorPosition(1)
        }
    }
}

/// Event-tap callbacks get one raw pointer back. A callback macOS had already queued when its owner stopped would read
/// freed memory through a pointer (the crash seen in the Xcode test host, EXC_BAD_ACCESS calling a dead closure). So the
/// pointer is only a token, looked up here; `remove` makes any late callback find nothing.
final class TapOwners<T: AnyObject>: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [UInt: T] = [:]
    private var next: UInt = 1

    func add(_ owner: T) -> UInt {
        lock.lock(); defer { lock.unlock() }
        let id = next
        next += 1
        items[id] = owner
        return id
    }

    func get(_ info: UnsafeMutableRawPointer?) -> T? {
        guard let info else { return nil }
        lock.lock(); defer { lock.unlock() }
        return items[UInt(bitPattern: info)]
    }

    func remove(_ id: UInt) {
        lock.lock(); items[id] = nil; lock.unlock()
    }
}

/// Listens (never changes) for keys and mouse moves that are not Misty's own. Needs the Input Monitoring permission;
/// without it `start()` is false and take-over detection simply does not run.
public final class UserInputTap: @unchecked Sendable {
    private let onInput: @Sendable (Double) -> Void
    private let lock = NSLock()
    private var thread: Thread?
    private var runLoop: CFRunLoop?
    private var port: CFMachPort?
    private var token: UInt?                             // how the tap's callback finds us; gone after stop()
    private static let owners = TapOwners<UserInputTap>()
    private var lastSent = 0.0

    public init(onInput: @escaping @Sendable (Double) -> Void) { self.onInput = onInput }

    fileprivate func turnBackOn() {
        lock.lock(); let tap = port; lock.unlock()
        if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
    }

    fileprivate func seen(_ event: CGEvent) {
        if event.getIntegerValueField(.eventSourceUserData) == Input.tag { return }
        let now = Date().timeIntervalSince1970
        if now - lastSent < 0.2 { return }
        lastSent = now
        onInput(now)
    }

    public func start() -> Bool {
        guard CGPreflightListenEventAccess() else { return false }
        lock.lock(); defer { lock.unlock() }
        if thread != nil { return true }
        let mask: CGEventMask = [CGEventType.keyDown, .leftMouseDown, .rightMouseDown, .scrollWheel, .mouseMoved]
            .reduce(0) { $0 | (1 << CGEventMask($1.rawValue)) }
        let callback: CGEventTapCallBack = { _, type, event, info in
            guard let me = UserInputTap.owners.get(info) else { return Unmanaged.passUnretained(event) }
            // macOS switches a tap off when the Mac is too busy to serve it (a build, a test run) and says so with a
            // callback that carries no event at all. Touching that event is the 0x0 crash seen in `seen`.
            if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput { me.turnBackOn(); return nil }
            me.seen(event)
            return Unmanaged.passUnretained(event)
        }
        let id = Self.owners.add(self)
        guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                                          eventsOfInterest: mask, callback: callback,
                                          userInfo: UnsafeMutableRawPointer(bitPattern: id)) else {
            Self.owners.remove(id)
            return false
        }
        token = id
        port = tap
        let t = Thread { [weak self] in
            let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
            CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
            self?.lock.lock(); self?.runLoop = CFRunLoopGetCurrent(); self?.lock.unlock()
            CGEvent.tapEnable(tap: tap, enable: true)
            CFRunLoopRun()
        }
        t.name = "misty.userinput"
        t.start()
        thread = t
        return true
    }

    /// Stops listening and lets go of the reference the callback was holding.
    public func stop() {
        lock.lock()
        let rl = runLoop, tap = port, id = token
        runLoop = nil; port = nil; token = nil; thread = nil
        lock.unlock()
        if let id { Self.owners.remove(id) }              // first: a callback already queued now finds nothing
        if let tap { CGEvent.tapEnable(tap: tap, enable: false) }
        if let rl { CFRunLoopStop(rl) }
    }
}
