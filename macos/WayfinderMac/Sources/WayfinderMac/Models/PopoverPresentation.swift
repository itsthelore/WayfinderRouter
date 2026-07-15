import Foundation

struct PopoverPresentation: Equatable {
    let overallStatus: String
    let endpoints: EndpointStatusPresentation
    let routing: RoutingPopoverPresentation

    init(overview: GatewayOverview) {
        self.overallStatus = Self.overallStatus(
            gateway: overview.gateway,
            hosted: overview.hosted
        )
        self.endpoints = EndpointStatusPresentation(overview: overview)
        self.routing = RoutingPopoverPresentation(stats: overview.routingStats)
    }

    private static func overallStatus(
        gateway: GatewayDisplayState,
        hosted: HostedDisplayState
    ) -> String {
        guard case .running = gateway else {
            return gateway.title
        }

        switch hosted {
        case .checking:
            return "Checking"
        case .checkKeys, .noModels, .unavailable:
            return "Degraded"
        case .ready, .disabled:
            return "Running"
        }
    }

}

struct EndpointStatusPresentation: Equatable {
    let endpoints: [EndpointDisplayStatus]
    let summary: String
    let emptyMessage: String?
    let accessibilityLabel: String

    init(overview: GatewayOverview) {
        self.endpoints = overview.endpoints

        if overview.endpoints.isEmpty {
            switch overview.gateway {
            case .checking:
                self.summary = "Checking"
                self.emptyMessage = "Checking endpoint status…"
            case .running, .degraded, .offline:
                self.summary = "None"
                self.emptyMessage = "No configured endpoints"
            case .stopped, .unreachable, .notInstalled:
                self.summary = "Unavailable"
                self.emptyMessage = "Endpoint status unavailable"
            }
            self.accessibilityLabel = "Endpoint Status, \(summary)"
            return
        }

        self.emptyMessage = nil
        let attentionCount = overview.endpoints.filter { $0.state != .ready }.count
        if attentionCount == 0 {
            self.summary = "Ready"
        } else {
            self.summary = "\(attentionCount) issue\(attentionCount == 1 ? "" : "s")"
        }

        let details = overview.endpoints
            .map { "\($0.providerName), \($0.state.title)" }
            .joined(separator: ", ")
        self.accessibilityLabel = "Endpoint Status, \(summary). \(details)"
    }
}

enum EndpointSubmenuSizing {
    static let width: CGFloat = 280
    static let rowHeight: CGFloat = 44
    static let verticalPadding: CGFloat = 8
    static let maximumHeight: CGFloat = 360

    static func contentHeight(rowCount: Int) -> CGFloat {
        let rows = max(1, rowCount)
        return min(maximumHeight, CGFloat(rows) * rowHeight + verticalPadding)
    }
}

enum EndpointSubmenuPlacement {
    static func frame(
        anchorFrame: CGRect,
        parentFrame: CGRect,
        visibleFrame: CGRect,
        size: CGSize,
        gap: CGFloat = 4,
        inset: CGFloat = 8
    ) -> CGRect {
        let minimumX = visibleFrame.minX + inset
        let maximumX = visibleFrame.maxX - size.width - inset
        let rightX = parentFrame.maxX + gap
        let leftX = parentFrame.minX - size.width - gap

        let x: CGFloat
        if rightX <= maximumX {
            x = rightX
        } else if leftX >= minimumX {
            x = leftX
        } else {
            let availableRight = visibleFrame.maxX - parentFrame.maxX
            let availableLeft = parentFrame.minX - visibleFrame.minX
            let preferred = availableRight >= availableLeft ? rightX : leftX
            x = min(max(preferred, minimumX), max(minimumX, maximumX))
        }

        let minimumY = visibleFrame.minY + inset
        let maximumY = visibleFrame.maxY - size.height - inset
        let alignedY = anchorFrame.maxY - size.height
        let y = min(max(alignedY, minimumY), max(minimumY, maximumY))

        return CGRect(origin: CGPoint(x: x, y: y), size: size)
    }
}

struct RoutingPopoverPresentation: Equatable {
    let hasDecisions: Bool
    let localFraction: Double
    let totalText: String?
    let localText: String
    let cloudText: String
    let accessibilityLabel: String

    init(stats: RoutingStats) {
        let countTotal = (stats.localRouteCount ?? 0) + (stats.cloudRouteCount ?? 0)
        let total = stats.totalTurns ?? countTotal
        let hasDecisions = total > 0
        let localFraction = min(1, max(0, stats.localPercent))

        self.hasDecisions = hasDecisions
        self.localFraction = localFraction
        self.totalText = hasDecisions ? "\(total) turn\(total == 1 ? "" : "s")" : nil

        if hasDecisions,
           let localCount = stats.localRouteCount,
           let cloudCount = stats.cloudRouteCount {
            self.localText = "Local \(localCount) · \(stats.localPercent.percentText)"
            self.cloudText = "Cloud \(cloudCount) · \(stats.cloudPercent.percentText)"
        } else if hasDecisions {
            self.localText = "Local \(stats.localPercent.percentText)"
            self.cloudText = "Cloud \(stats.cloudPercent.percentText)"
        } else {
            self.localText = "No recent decisions"
            self.cloudText = ""
        }

        let mixText = cloudText.isEmpty ? localText : "\(localText), \(cloudText)"
        self.accessibilityLabel = totalText.map { "Routing, \($0), \(mixText)" }
            ?? "Routing, no recent decisions"
    }
}

enum PopoverPanelSizing {
    static let targetWidth: CGFloat = 340
    static let maximumHeight: CGFloat = 420
    static let minimumHeight: CGFloat = 1

    static func clampedHeight(_ fittingHeight: CGFloat) -> CGFloat {
        min(maximumHeight, max(minimumHeight, ceil(fittingHeight)))
    }
}

enum PopoverPanelPlacement {
    static func leftAlignedX(
        anchorMinX: CGFloat,
        visibleMinX: CGFloat,
        visibleMaxX: CGFloat,
        panelWidth: CGFloat,
        inset: CGFloat
    ) -> CGFloat {
        let minimumX = visibleMinX + inset
        let maximumX = visibleMaxX - panelWidth - inset
        guard maximumX >= minimumX else {
            return minimumX
        }
        return min(max(anchorMinX, minimumX), maximumX)
    }
}
