import AppKit

/// Hold Right Option (MacBook) or Right Control (Aula F75) to dictate. Released: done. Tap `/` while holding: locks
/// it on, hands-free, and the next `/` ends it. Ported from Ripple 2's HotkeyMonitor, including what it learned:
///  - `.maskAlternate` can't tell left from right, so the raw device bits are used (0x40 right option, 0x2000 right control).
///  - A modifier can blip off for a few ms; releases wait 0.4 s before counting.
///  - macOS switches slow event taps off; a 5 s watchdog turns it back on.
/// Misty's own wake chord is Left Control + Left Option, so the two never fire together.
@MainActor
public final class DictationKey {
    static let triggerBits: UInt64 = 0x40 | 0x2000
    static let debounce: Duration = .milliseconds(400)
    static let lockKeyCode: Int64 = 44                  // "/" on a US layout

    var onPress: (() -> Void)?
    var onRelease: (() -> Void)?
    var onLock: ((Bool) -> Void)?                        // true: latched on, false: latched off (finish)
    var onCancel: (() -> Void)?                          // another key joined in: it was a shortcut, not dictation

    private(set) var isLocked = false
    private var isHeld = false
    private var typedWhileHeld = false
    private var pendingRelease: Task<Void, Never>?
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var watchdog: Timer?

    public init() {}

    var running: Bool { tap != nil }

    /// False if macOS refused the tap (Accessibility is missing).
    @discardableResult
    func start() -> Bool {
        guard tap == nil else { return true }
        // a default tap, not listen-only: the `/` that locks must not also type a slash into what is being dictated
        let mask: CGEventMask = (1 << CGEventType.flagsChanged.rawValue) | (1 << CGEventType.keyDown.rawValue)
        let callback: CGEventTapCallBack = { _, type, event, refcon in
            guard let refcon else { return Unmanaged.passUnretained(event) }
            let me = Unmanaged<DictationKey>.fromOpaque(refcon).takeUnretainedValue()
            switch type {
            case .flagsChanged:
                let flags = event.flags.rawValue
                MainActor.assumeIsolated { me.handle(flags: flags) }
            case .keyDown:
                if event.getIntegerValueField(.eventSourceUserData) == Input.tag { break }   // keys Misty posted herself (our paste)
                let code = event.getIntegerValueField(.keyboardEventKeycode)
                if MainActor.assumeIsolated({ me.handleKeyDown(code) }) { return nil }       // swallowed
            case .tapDisabledByTimeout, .tapDisabledByUserInput:
                MainActor.assumeIsolated { me.reenable() }
            default: break
            }
            return Unmanaged.passUnretained(event)
        }
        guard let t = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .defaultTap,
                                        eventsOfInterest: mask, callback: callback,
                                        userInfo: Unmanaged.passUnretained(self).toOpaque()) else { return false }
        tap = t
        source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, t, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: t, enable: true)
        watchdog = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.reenable() }
        }
        VoiceLog.write("dictation key on (Right Option / Right Control)")
        return true
    }

    func stop() {
        watchdog?.invalidate(); watchdog = nil
        pendingRelease?.cancel(); pendingRelease = nil
        if let tap { CGEvent.tapEnable(tap: tap, enable: false) }
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        tap = nil; source = nil
        isHeld = false; isLocked = false
    }

    private func reenable() {
        guard let tap, !CGEvent.tapIsEnabled(tap: tap) else { return }
        CGEvent.tapEnable(tap: tap, enable: true)
        VoiceLog.write("dictation key tap was switched off by macOS, switched back on")
    }

    /// True if the key was used up here and must not reach the app.
    private func handleKeyDown(_ code: Int64) -> Bool {
        guard isHeld || isLocked else { return false }
        guard code == Self.lockKeyCode else {
            if isHeld, !isLocked { typedWhileHeld = true }       // Option+letter and the like: a shortcut
            return false
        }
        isLocked.toggle()
        if !isLocked { isHeld = false; pendingRelease?.cancel(); pendingRelease = nil }
        onLock?(isLocked)
        return true
    }

    private func handle(flags: UInt64) {
        if isLocked { return }                                   // latched: the trigger key no longer matters
        let held = flags & Self.triggerBits != 0
        if held {
            if pendingRelease != nil {                           // came back inside the debounce: a blip, keep going
                pendingRelease?.cancel(); pendingRelease = nil
                return
            }
            guard !isHeld else { return }
            isHeld = true
            typedWhileHeld = false
            onPress?()
        } else {
            guard isHeld, pendingRelease == nil else { return }
            isHeld = false
            pendingRelease = Task { [weak self] in
                try? await Task.sleep(for: Self.debounce)
                guard !Task.isCancelled, let self else { return }
                self.pendingRelease = nil
                if self.typedWhileHeld { self.typedWhileHeld = false; self.onCancel?() } else { self.onRelease?() }
            }
        }
    }
}
