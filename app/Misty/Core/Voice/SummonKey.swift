import Foundation
#if canImport(AppKit)
import AppKit
#endif

/// Control+Option or Command+Option tapped on its own: both down, nothing else pressed, both up within `maxHold`.
/// Ctrl+Space (Siri), Ctrl+Option+key shortcuts and Option alone (Whisper) never count.
public struct ChordDetector: Sendable {
    public struct Mods: Equatable, Sendable {
        public var control = false, option = false, command = false, shift = false
        public var other = false          // a right-hand modifier: dictation's keys, never part of this chord
        public init(control: Bool = false, option: Bool = false, command: Bool = false, shift: Bool = false, other: Bool = false) {
            self.control = control; self.option = option; self.command = command; self.shift = shift; self.other = other
        }
        var none: Bool { !control && !option && !command && !shift && !other }
    }

    public var maxHold: TimeInterval = 0.6
    private var armedAt: TimeInterval?
    private var spoiled = false          // a key or another modifier joined in: wait until everything is up again

    public init() {}

    /// Returns true on the release that completes a tap.
    public mutating func flags(_ m: Mods, at t: TimeInterval) -> Bool {
        if m.none {
            defer { armedAt = nil; spoiled = false }
            guard let a = armedAt, !spoiled else { return false }
            return t - a <= maxHold
        }
        if m.shift || m.other || (m.command && m.control) { spoiled = true; return false }
        if m.option && (m.control || m.command) && armedAt == nil && !spoiled { armedAt = t }
        return false
    }

    /// A key went down while a modifier was held.
    public mutating func key(at t: TimeInterval) { spoiled = true }
}

#if canImport(AppKit)
/// Watches the keyboard for the summon tap, in every app including Misty herself. A listen-only event tap on its own
/// thread (NSEvent monitors miss keys while Misty is the front app and has no key window). Falls back to NSEvent
/// monitors when Input Monitoring isn't granted. Keys Misty posts herself (Stream) never count.
@MainActor
public final class SummonKey {
    private var detector = ChordDetector()
    private var monitors: [Any] = []
    private let onTap: @MainActor () -> Void
    private var held = false            // any modifier down: only then can a keyDown spoil a chord
    private let tap = KeyTap()

    public init(onTap: @escaping @MainActor () -> Void) { self.onTap = onTap }

    static let logKeys = VoiceLog.debug                          // modifier changes only, never letters
    /// Timing only (card A3): when the tap thread first saw the event that fired the last summon, for key-to-silence.
    public static var lastFiredAt: ContinuousClock.Instant?

    public func start() {
        guard monitors.isEmpty, !tap.running else { return }
        let viaTap = tap.start(onEvent: { [weak self] isKey, flags, t in
            let seen = ContinuousClock.now                          // timing only: before the hop to the main thread
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.handle(isKey: isKey, flags: flags, at: t, seen: seen) } }  // FIFO: a chord needs its events in order
        })
        VoiceLog.write("summon key via \(viaTap ? "event tap" : "NSEvent monitors")")
        if viaTap { return }
        let handler: (NSEvent) -> Void = { [weak self] e in
            MainActor.assumeIsolated {
                self?.handle(isKey: e.type == .keyDown, flags: CGEventFlags(rawValue: UInt64(e.modifierFlags.rawValue)), at: e.timestamp)
            }
        }
        if let g = NSEvent.addGlobalMonitorForEvents(matching: [.flagsChanged, .keyDown], handler: handler) {
            monitors.append(g)
        }
        if let l = NSEvent.addLocalMonitorForEvents(matching: [.flagsChanged, .keyDown], handler: { e in
            handler(e); return e
        }) { monitors.append(l) }
    }

    public func stop() {
        tap.stop()
        monitors.forEach(NSEvent.removeMonitor)
        monitors = []
    }

    private func handle(isKey: Bool, flags f: CGEventFlags, at t: TimeInterval, seen: ContinuousClock.Instant = .now) {
        if isKey {
            if held { detector.key(at: t) }
            return
        }
        // Left-hand Option with left Control or left Command. Ripple 2 (dictation) starts on Right Option or Right Control,
        // so counting those here fired both apps at once. A right-hand key held counts as "something else joined in"
        // and spoils the tap.
        let raw = f.rawValue
        let right = raw & (0x40 | 0x2000 | 0x10) != 0          // NX_DEVICERALTKEYMASK | NX_DEVICERCTLKEYMASK | NX_DEVICERCMDKEYMASK
        let m = ChordDetector.Mods(control: raw & 0x1 != 0, option: raw & 0x20 != 0,       // left control, left option
                                   command: raw & 0x8 != 0, shift: f.contains(.maskShift), other: right)
        held = !m.none
        let fired = detector.flags(m, at: t)
        if Self.logKeys { VoiceLog.write("key ctrl=\(m.control) opt=\(m.option) cmd=\(m.command) shift=\(m.shift) fired=\(fired)") }
        if fired { Self.lastFiredAt = seen; onTap() }
    }
}

/// A listen-only CGEvent tap for flagsChanged and keyDown, run on its own thread.
final class KeyTap: @unchecked Sendable {
    typealias Handler = @Sendable (_ isKey: Bool, _ flags: CGEventFlags, _ seconds: TimeInterval) -> Void
    private let lock = NSLock()
    private var port: CFMachPort?
    private var runLoop: CFRunLoop?
    private var handler: Handler?
    private var token: UInt?
    private static let owners = TapOwners<KeyTap>()

    var running: Bool { lock.lock(); defer { lock.unlock() }; return port != nil }

    fileprivate func seen(_ type: CGEventType, _ e: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            lock.lock(); let p = port; lock.unlock()
            if let p { CGEvent.tapEnable(tap: p, enable: true) }    // macOS switches slow taps off; switch it back on
            return
        }
        if e.getIntegerValueField(.eventSourceUserData) == Input.tag { return }
        handler?(type == .keyDown, e.flags, TimeInterval(e.timestamp) / 1_000_000_000)
    }

    func start(onEvent: @escaping Handler) -> Bool {
        guard CGPreflightListenEventAccess() else { return false }
        lock.lock(); defer { lock.unlock() }
        if port != nil { return true }
        handler = onEvent
        let mask: CGEventMask = [CGEventType.flagsChanged, .keyDown].reduce(0) { $0 | (1 << CGEventMask($1.rawValue)) }
        let callback: CGEventTapCallBack = { _, type, event, info in
            KeyTap.owners.get(info)?.seen(type, event)         // nil once stopped: a late callback does nothing
            return Unmanaged.passUnretained(event)
        }
        let id = Self.owners.add(self)
        guard let p = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                                        eventsOfInterest: mask, callback: callback,
                                        userInfo: UnsafeMutableRawPointer(bitPattern: id)) else {
            Self.owners.remove(id)
            return false
        }
        token = id
        port = p
        let t = Thread { [weak self] in
            let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, p, 0)
            CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
            self?.lock.lock(); self?.runLoop = CFRunLoopGetCurrent(); self?.lock.unlock()
            CGEvent.tapEnable(tap: p, enable: true)
            CFRunLoopRun()
        }
        t.name = "misty.summonkey"
        t.start()
        return true
    }

    func stop() {
        lock.lock()
        let rl = runLoop, p = port, id = token
        runLoop = nil; port = nil; token = nil; handler = nil
        lock.unlock()
        if let id { Self.owners.remove(id) }
        if let p { CGEvent.tapEnable(tap: p, enable: false) }
        if let rl { CFRunLoopStop(rl) }
    }
}
#endif
