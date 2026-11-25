import AudioToolbox
import CoreAudio
import Foundation

/// An app that has the mic open right now (macOS lists this for every process, no permission needed).
public struct MicUser: Equatable, Sendable {
    public var bundleID: String
    public init(bundleID: String) { self.bundleID = bundleID }
}

/// Which apps count as "he is in a call". Prefix matches, so a browser's helper processes (Meet runs in one) count too.
public enum MeetingApps {
    static let known: [(prefix: String, name: String)] = [
        ("us.zoom.xos", "Zoom"), ("com.microsoft.teams", "Teams"), ("com.apple.FaceTime", "FaceTime"),
        ("net.whatsapp.WhatsApp", "WhatsApp"), ("com.tinyspeck.slackmacgap", "Slack"), ("com.hnc.Discord", "Discord"),
        ("com.google.Chrome", "Chrome"), ("com.apple.Safari", "Safari"),
        ("com.microsoft.edgemac", "Edge"), ("org.mozilla.firefox", "Firefox"), ("com.brave.Browser", "Brave"),
        ("com.cisco.webexmeetingsapp", "Webex"), ("com.apple.WebKit", "Safari"),
    ]

    public static func name(forBundle id: String) -> String? {
        known.first { id.hasPrefix($0.prefix) }?.name
    }

    /// The first call app among the processes using the mic, else nil.
    public static func inCall(_ users: [MicUser]) -> String? {
        for u in users { if let n = name(forBundle: u.bundleID) { return n } }
        return nil
    }
}

/// Turns "is a call app on the mic?" (asked every few seconds) into one prompt per call. A call is the mic in use for
/// `holdFor` seconds, so a notification sound doesn't count; it is over after `quietFor` seconds of the mic being free,
/// so a mute in the middle doesn't start a second prompt.
public struct MeetingWatch: Sendable {
    public var holdFor: TimeInterval = 10
    public var quietFor: TimeInterval = 60
    private var since: Date?
    private var current: String?
    private var lastActive: Date?
    private var prompted = false

    public init() {}

    /// The app to ask about now, or nil.
    public mutating func step(app: String?, now: Date) -> String? {
        if let app {
            lastActive = now
            if current != app { current = app; since = now }
            if !prompted, let s = since, now.timeIntervalSince(s) >= holdFor {
                prompted = true
                return app
            }
            return nil
        }
        if let l = lastActive, now.timeIntervalSince(l) >= quietFor {
            current = nil; since = nil; lastActive = nil; prompted = false
        }
        return nil
    }
}

/// Reads the live list of processes using the mic from Core Audio (macOS 14.2+).
public enum MicUsers {
    public static func current() -> [MicUser] {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                              mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        let sys = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(sys, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(sys, &addr, 0, nil, &size, &ids) == noErr else { return [] }
        var out: [MicUser] = []
        for id in ids {
            var running: UInt32 = 0
            var rsize = UInt32(MemoryLayout<UInt32>.size)
            var ra = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyIsRunningInput,
                                                mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            guard AudioObjectGetPropertyData(id, &ra, 0, nil, &rsize, &running) == noErr, running != 0 else { continue }
            var bundle: Unmanaged<CFString>?
            var bsize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            var ba = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyBundleID,
                                                mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            guard AudioObjectGetPropertyData(id, &ba, 0, nil, &bsize, &bundle) == noErr, let b = bundle?.takeRetainedValue() else { continue }
            out.append(MicUser(bundleID: b as String))
        }
        return out
    }
}
