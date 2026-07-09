import SwiftUI

public struct RoutingSummarySection: View {
    let stats: RoutingStats

    public init(stats: RoutingStats) {
        self.stats = stats
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Label("Routing", systemImage: "arrow.left.arrow.right")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.primary)

                Spacer()

                Text(totalText)
                    .font(.system(size: 13, weight: .regular).monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            SplitRouteBar(localPercent: stats.localPercent)

            Text(routeMixText)
                .font(.system(size: 14, weight: .regular))
                .foregroundStyle(.primary)

            Text(routeCountText)
                .font(.system(size: 13, weight: .regular))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Routing, \(routeMixText), \(routeCountText)")
    }

    private var totalText: String {
        guard let total = stats.totalTurns, total > 0 else {
            return "No turns"
        }
        return "\(total) turn\(total == 1 ? "" : "s")"
    }

    private var routeMixText: String {
        guard let total = stats.totalTurns, total > 0 else {
            return "No recent routing decisions"
        }
        return "Local \(stats.localPercent.percentText) · Cloud \(stats.cloudPercent.percentText)"
    }

    private var routeCountText: String {
        guard let local = stats.localRouteCount, let cloud = stats.cloudRouteCount else {
            return "Routed: not yet available"
        }
        return "Routed: local: \(local) · cloud: \(cloud)"
    }
}

private struct SplitRouteBar: View {
    let localPercent: Double

    var body: some View {
        GeometryReader { proxy in
            let clampedLocal = min(1, max(0, localPercent))
            let localWidth = max(0, proxy.size.width * CGFloat(clampedLocal))

            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.primary.opacity(0.10))

                HStack(spacing: 0) {
                    Rectangle()
                        .fill(WayfinderTheme.local.opacity(0.86))
                        .frame(width: localWidth)
                    Rectangle()
                        .fill(WayfinderTheme.cloud.opacity(0.74))
                }
                .clipShape(Capsule())
            }
        }
        .frame(height: 6)
        .accessibilityHidden(true)
    }
}
