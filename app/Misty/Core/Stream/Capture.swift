import Foundation
import AppKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

/// One window of one app as a PNG. ScreenCaptureKit's captureImage hung inside Misty (HANDS_TIMEOUT in hands-v2 and on
/// 2026-10-21), so this finds the window with CGWindowList and asks the system `screencapture` tool for just that window.
/// The PNG goes to a private temp file that is read and deleted at once.
public enum Capture {
    static func windowNumber(pid: Int) -> Int? {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else {
            return nil
        }
        let mine = list.filter { ($0[kCGWindowOwnerPID as String] as? Int) == pid && ($0[kCGWindowLayer as String] as? Int) == 0 }
        func area(_ w: [String: Any]) -> Double {
            let b = w[kCGWindowBounds as String] as? [String: Double] ?? [:]
            return (b["Width"] ?? 0) * (b["Height"] ?? 0)
        }
        return mine.max(by: { area($0) < area($1) })?[kCGWindowNumber as String] as? Int
    }

    public static func windowPNG(pid: Int) async throws -> Data {
        guard let num = windowNumber(pid: pid) else { throw HandsError.code("NO_WINDOW") }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("stream-\(UUID().uuidString).png")
        defer { try? FileManager.default.removeItem(at: url) }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        p.arguments = ["-x", "-o", "-l\(num)", "-t", "png", url.path]
        try p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0, let data = try? Data(contentsOf: url), !data.isEmpty else {
            throw HandsError.code("CAPTURE_FAILED")
        }
        return try shrink(data)
    }

    /// Retina windows are huge: send at most 1568 px on the long side as JPEG, which is what vision models use anyway
    /// (and keeps the hands.result line small).
    static func shrink(_ data: Data, maxPixels: Int = 1568) throws -> Data {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil),
              let img = CGImageSourceCreateThumbnailAtIndex(src, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: maxPixels,
                kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else { throw HandsError.code("CAPTURE_FAILED") }
        let out = NSMutableData()
        guard let dst = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil) else { throw HandsError.code("CAPTURE_FAILED") }
        CGImageDestinationAddImage(dst, img, [kCGImageDestinationLossyCompressionQuality: 0.7] as CFDictionary)
        guard CGImageDestinationFinalize(dst) else { throw HandsError.code("CAPTURE_FAILED") }
        return out as Data
    }
}
