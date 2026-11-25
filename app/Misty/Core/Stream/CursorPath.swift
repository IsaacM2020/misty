import CoreGraphics
import Foundation

/// The curve Misty's own cursor takes between two points on screen (Hands v2 Task 18): a quadratic Bezier whose control
/// point sits 18% of the distance off the middle, to the left of the direction of travel, so the glide arcs like a hand
/// and not like a ruler. Screen coordinates, y pointing down.
public struct CursorPath: Equatable, Sendable {
    public let from: CGPoint
    public let to: CGPoint
    private let control: CGPoint

    public init(from: CGPoint, to: CGPoint) {
        self.from = from
        self.to = to
        let dx = Double(to.x - from.x), dy = Double(to.y - from.y)
        let d = (dx * dx + dy * dy).squareRoot()
        let mid = CGPoint(x: (from.x + to.x) / 2, y: (from.y + to.y) / 2)
        if d < 0.0001 {
            control = mid
        } else {
            // y points down, so the left of travel (dx, dy) is (dy, -dx)
            control = CGPoint(x: Double(mid.x) + dy / d * 0.18 * d, y: Double(mid.y) - dx / d * 0.18 * d)
        }
    }

    /// Where the cursor is a fraction `t` (0...1) of the way along.
    public func point(at t: Double) -> CGPoint {
        let u = 1 - t
        return CGPoint(x: u * u * Double(from.x) + 2 * u * t * Double(control.x) + t * t * Double(to.x),
                       y: u * u * Double(from.y) + 2 * u * t * Double(control.y) + t * t * Double(to.y))
    }

    /// Which way the arrow points at `t`, in radians, along the tangent of the curve (0 is to the right, positive turns down the screen).
    public func heading(at t: Double) -> Double {
        let dx = 2 * (1 - t) * Double(control.x - from.x) + 2 * t * Double(to.x - control.x)
        let dy = 2 * (1 - t) * Double(control.y - from.y) + 2 * t * Double(to.y - control.y)
        return atan2(dy, dx)
    }

    /// Seconds the glide takes: a short hop is quick, a long one is not hurried.
    public var duration: Double {
        let d = (Double(to.x - from.x) * Double(to.x - from.x) + Double(to.y - from.y) * Double(to.y - from.y)).squareRoot()
        return min(0.9, max(0.35, 0.35 + d / 2400))
    }
}
