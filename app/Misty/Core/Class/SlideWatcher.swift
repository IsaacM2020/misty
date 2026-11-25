import AppKit
import CoreGraphics
import Foundation
import ImageIO
import Vision

/// The shared screen of an online class, read as text on this Mac (Apple's Vision OCR). Nothing is sent to a model
/// from here and the picture is never kept: the window is grabbed to a private temp file, read, and deleted at once.
/// It only looks at a meeting window (Zoom, Teams, Webex, Zoho, or a browser tab that says Meet / Zoom / Teams / ...),
/// so a chat or an email open beside the class is never read. A new slide is reported only when the picture changed
/// and its words differ from the last slide.
public actor SlideWatcher {
    public struct Win: Equatable, Sendable {
        public var number: Int
        public var owner: String
        public var title: String
        public var w: Double
        public var h: Double
        public init(number: Int, owner: String, title: String, w: Double, h: Double) {
            self.number = number; self.owner = owner; self.title = title; self.w = w; self.h = h
        }
    }

    static let meetingApps = ["zoom.us", "microsoft teams", "webex", "zoho"]
    static let browsers = ["google chrome", "safari", "firefox", "microsoft edge", "brave browser"]
    static let titleHints = ["meet", "zoom", "teams", "webex", "zoho", "classroom", "lecture", "webinar"]
    static let chrome: Set<String> = ["mute", "unmute", "start video", "stop video", "participants", "chat", "share screen",
                                      "record", "reactions", "leave", "end", "more", "security", "raise hand", "apps",
                                      "whiteboards", "show chat", "stop share", "pause share", "new share"]

    private var lastHash: [UInt8] = []
    private var lastText = ""

    public init() {}

    public func reset() { lastHash = []; lastText = "" }

    // MARK: pure rules (tested)

    /// The front-most window that is a class: big enough to be a shared screen, and a meeting app or a browser tab
    /// whose title looks like a call. Misty's own windows never count.
    nonisolated static func pick(_ wins: [Win]) -> Win? {
        wins.first { w in
            guard w.w >= 400, w.h >= 250 else { return false }
            let owner = w.owner.lowercased(), title = w.title.lowercased()
            if owner == "misty" { return false }
            if meetingApps.contains(where: { owner.contains($0) }) { return true }
            return browsers.contains(owner) && titleHints.contains { title.contains($0) }
        }
    }

    /// OCR lines without the call's own buttons, one string.
    nonisolated static func clean(_ lines: [String]) -> String {
        let kept = lines.map { $0.trimmingCharacters(in: .whitespaces) }.filter { l in
            l.count >= 3 && l.contains(where: \.isLetter) && !chrome.contains(l.lowercased())
        }
        return String(kept.joined(separator: " ").prefix(1200))
    }

    /// A new slide: at least 4 words, and fewer than 70% of its words shared with the last slide.
    nonisolated static func isNewSlide(old: String, new: String) -> Bool {
        func words(_ s: String) -> Set<String> {
            Set(s.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init))
        }
        let b = words(new)
        guard b.count >= 4 else { return false }
        let a = words(old)
        guard !a.isEmpty else { return true }
        return Double(a.intersection(b).count) / Double(a.union(b).count) < 0.7
    }

    /// Mean absolute difference of two thumbnails, 0...255.
    nonisolated static func distance(_ a: [UInt8], _ b: [UInt8]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return 255 }
        var sum = 0
        for i in a.indices { sum += abs(Int(a[i]) - Int(b[i])) }
        return Double(sum) / Double(a.count)
    }

    // MARK: looking

    /// The text of the shared screen if it is a new slide since the last call, else nil. Cheap when nothing changed:
    /// a 32x18 thumbnail compare decides whether the OCR runs at all.
    public func poll() async -> String? {
        let snap = await Task.detached(priority: .utility) { () -> (hash: [UInt8], image: CGImage)? in
            guard let win = Self.pick(Self.windows()), let img = Self.grab(win.number) else { return nil }
            return (Self.thumb(img), img)
        }.value
        guard let snap, Self.distance(lastHash, snap.hash) > 1.5 else { return nil }
        lastHash = snap.hash
        let image = snap.image
        let text = await Task.detached(priority: .utility) { Self.read(image) }.value
        guard Self.isNewSlide(old: lastText, new: text) else { return nil }
        lastText = text
        return text
    }

    nonisolated static func windows() -> [Win] {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else {
            return []
        }
        return list.compactMap { w in
            guard (w[kCGWindowLayer as String] as? Int) == 0, let num = w[kCGWindowNumber as String] as? Int else { return nil }
            let b = w[kCGWindowBounds as String] as? [String: Double] ?? [:]
            return Win(number: num, owner: w[kCGWindowOwnerName as String] as? String ?? "",
                       title: w[kCGWindowName as String] as? String ?? "", w: b["Width"] ?? 0, h: b["Height"] ?? 0)
        }
    }

    /// The window as an image, through the system `screencapture` tool (ScreenCaptureKit hung inside Misty before; see
    /// Capture.swift). Shrunk to 1800 px on the long side, which is plenty for slide text.
    nonisolated static func grab(_ number: Int) -> CGImage? {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("slide-\(UUID().uuidString).png")
        defer { try? FileManager.default.removeItem(at: url) }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        p.arguments = ["-x", "-o", "-l\(number)", "-t", "png", url.path]
        guard (try? p.run()) != nil else { return nil }
        p.waitUntilExit()
        guard p.terminationStatus == 0, let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        return CGImageSourceCreateThumbnailAtIndex(src, 0, [
            kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 1800,
            kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary)
    }

    nonisolated static func thumb(_ img: CGImage) -> [UInt8] {
        let w = 32, h = 18
        var px = [UInt8](repeating: 0, count: w * h)
        guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return px }
        ctx.interpolationQuality = .low
        ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
        return px
    }

    nonisolated static func read(_ img: CGImage) -> String {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = true
        try? VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
        return clean((req.results ?? []).compactMap { $0.topCandidates(1).first?.string })
    }
}
