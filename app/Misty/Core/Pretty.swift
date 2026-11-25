import Foundation

/// Small display fixes shared by the screens: machine-shaped values shown the way a person would write them.
public enum Pretty {
    private static let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    /// "2026-11-03" as "3 Nov". Anything else comes back as it was.
    public static func day(_ iso: String) -> String {
        let p = iso.prefix(10).split(separator: "-")
        guard p.count == 3, p[0].count == 4, let m = Int(p[1]), (1...12).contains(m), let d = Int(p[2]), (1...31).contains(d) else { return iso }
        return "\(d) \(months[m - 1])"
    }

    /// How often a follow is checked: "every 15 min", "every 3 h", "every 90 min".
    public static func every(_ minutes: Int) -> String {
        minutes >= 120 && minutes % 60 == 0 ? "every \(minutes / 60) h" : "every \(minutes) min"
    }

    /// A reply for the chat: the "One sec." she says out loud while a tool runs is dropped once the real answer is under it.
    public static func reply(_ text: String) -> String {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.hasPrefix(filler) else { return t }
        let rest = t.dropFirst(filler.count).trimmingCharacters(in: .whitespacesAndNewlines)
        return rest.isEmpty ? t : rest
    }
    static let filler = "One sec."
}
