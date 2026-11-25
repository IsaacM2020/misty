import Foundation

public struct PreparedNode: Equatable, Sendable {
    public var id: String
    public var label: String
    public var type: String
    public var area: String?
    public var importance: Int
    public var glow: Double
    public var radius: Double
    public var fixed: Bool
    public var x: Double
    public var y: Double
    public var areaX: Double
    public var areaY: Double
    public var lastDate: String?
    public var sources: Int = 0
    public var more: Int = 0            // area: "+N" entities not shown
    public var expanded: Bool = false
    public var members: Int = 0         // group: folded chat-only people
}

/// What the person has opened: areas expanded, an entity focused, a search typed. The engine does the rest.
public struct GraphViewState: Equatable, Sendable {
    public private(set) var expanded: [String] = []
    public private(set) var focusId: String?
    public private(set) var query: String = ""

    public init() {}

    public mutating func toggle(_ areaId: String) {
        if let i = expanded.firstIndex(of: areaId) { expanded.remove(at: i) } else { expanded.append(areaId) }
    }
    public mutating func focus(_ id: String?) { focusId = id }
    public mutating func search(_ text: String) { query = text.trimmingCharacters(in: .whitespaces) }

    public var params: GraphParams {
        GraphParams(expanded: expanded.sorted(), focus: focusId, q: query.isEmpty ? nil : query)
    }
}

public struct PreparedGraph: Equatable, Sendable {
    public var nodes: [PreparedNode]
    public var edges: [GraphEdge]
}

public enum GraphLayout {
    public static let ringRadius = 240.0
    public static let ringTwoRadius = 400.0
    public static let maxNodes = 400

    static func jitter(_ id: String, _ salt: Int) -> Double {
        var h: UInt64 = 1469598103934665603
        for b in (id + String(salt)).utf8 { h = (h ^ UInt64(b)) &* 1099511628211 }
        return Double(h % 2001) / 1000.0 - 1.0          // -1 ... 1
    }

    public static func prepare(_ g: GraphData) -> PreparedGraph {
        var nodes = g.nodes
        if nodes.count > maxNodes {
            // keep self and areas; drop the least important, quietest entities first
            let keep = nodes.filter { $0.type == "self" || $0.type == "area" }
            let rest = nodes.filter { $0.type != "self" && $0.type != "area" }
                .sorted { ($0.importance, $0.glow, $0.id) > ($1.importance, $1.glow, $1.id) }
            nodes = keep + Array(rest.prefix(max(0, maxNodes - keep.count)))
        }
        let areas = nodes.filter { $0.type == "area" }.sorted { ($0.importance, $1.id) > ($1.importance, $0.id) }
        let n = max(areas.count, 1)
        var areaPos: [String: (Double, Double)] = [:]
        for (i, a) in areas.enumerated() {
            let ang = Double(i) * 2 * .pi / Double(n)
            areaPos[a.id] = (ringRadius * cos(ang), ringRadius * sin(ang))
        }
        let gap = (Double(n) - 0.5) * 2 * .pi / Double(n)           // "other" sits in the ring's gap
        let other = (ringRadius * cos(gap), ringRadius * sin(gap))
        // ring 2: an area's entities spread inside its own sector (at most 80% of 2π/n), one slot each
        var slot: [String: (Int, Int)] = [:]                         // entity id -> (index, count in its area)
        var byArea: [String: [String]] = [:]
        for node in nodes where node.type != "self" && node.type != "area" && node.type != "group" {
            if let a = node.area, areaPos["areas/\(a)"] != nil { byArea[a, default: []].append(node.id) }
        }
        for (a, ids) in byArea {
            for (i, id) in ids.sorted().enumerated() { slot[id] = (i, ids.count) }
            _ = a
        }
        let sector = 2 * .pi / Double(n)
        // chat-only groups: small squares spread along the outer ring inside the gap, away from the area labels
        let groupIds = nodes.filter { $0.type == "group" }.map(\.id).sorted()
        let groupStep = groupIds.count > 1 ? min(sector * 0.8 / Double(groupIds.count - 1), 0.32) : 0
        var out: [PreparedNode] = []
        for node in nodes {
            let radius = 4 + 3 * Double(node.importance)
            let base = PreparedNode(id: node.id, label: node.label, type: node.type, area: node.area,
                                    importance: node.importance, glow: node.glow, radius: radius, fixed: false,
                                    x: 0, y: 0, areaX: 0, areaY: 0, lastDate: node.lastDate,
                                    sources: node.sources ?? 0, more: node.more ?? 0, expanded: node.expanded ?? false,
                                    members: node.members ?? 0)
            var p = base
            switch node.type {
            case "self":
                p.area = nil; p.fixed = true
            case "area":
                let pos = areaPos[node.id] ?? (0, 0)
                p.area = node.id; p.fixed = true; p.x = pos.0; p.y = pos.1; p.areaX = pos.0; p.areaY = pos.1
            case "group":
                let i = groupIds.firstIndex(of: node.id) ?? 0
                let ang = gap + (Double(i) - Double(groupIds.count - 1) / 2) * groupStep
                p.areaX = ringTwoRadius * cos(ang); p.areaY = ringTwoRadius * sin(ang)
                p.x = p.areaX + 6 * jitter(node.id, 1); p.y = p.areaY + 6 * jitter(node.id, 2)
            default:
                if let a = node.area, let ap = areaPos["areas/\(a)"], let (i, count) = slot[node.id] {
                    let centre = atan2(ap.1, ap.0)
                    let width = sector * 0.8
                    let step = count > 1 ? min(width / Double(count - 1), 0.32) : 0
                    let ang = centre + (Double(i) - Double(count - 1) / 2) * step
                    let r = ringTwoRadius + (i % 2 == 0 ? 0 : 36)       // stagger neighbours so labels do not touch
                    p.areaX = ringTwoRadius * cos(ang); p.areaY = ringTwoRadius * sin(ang)
                    p.x = r * cos(ang) + 10 * jitter(node.id, 1); p.y = r * sin(ang) + 10 * jitter(node.id, 2)
                } else {
                    p.x = other.0 + 40 * jitter(node.id, 1); p.y = other.1 + 40 * jitter(node.id, 2)
                    p.areaX = other.0; p.areaY = other.1
                }
            }
            out.append(p)
        }
        let ids = Set(out.map(\.id))
        return PreparedGraph(nodes: out, edges: g.edges.filter { ids.contains($0.source) && ids.contains($0.target) })
    }
}
