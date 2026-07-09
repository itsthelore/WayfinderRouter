import SwiftUI

public struct RoutingSummarySection: View {
    let stats: RoutingStats

    public init(stats: RoutingStats) {
        self.stats = stats
    }

    public var body: some View {
        HStack(spacing: NativeMenuMetrics.rowSpacing) {
            NativeMenuIconWell(symbolName: "arrow.left.arrow.right", tint: WayfinderTheme.local)

            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Routing")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(.primary)
                    Spacer()
                    Text(totalText)
                        .font(.system(size: 14, weight: .regular).monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                Text(routeMixText)
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                HStack(spacing: 10) {
                    SplitRouteBar(localPercent: stats.localPercent)
                        .frame(width: 118)
                    Text(routeCountText)
                        .font(.system(size: 12, weight: .regular))
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }
        }
        .frame(height: NativeMenuMetrics.metricRowHeight)
        .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
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
            return "Not yet available"
        }
        return "local: \(local) · cloud: \(cloud)"
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
                    .fill(Color.primary.opacity(0.12))

                HStack(spacing: 0) {
                    Rectangle()
                        .fill(WayfinderTheme.local.opacity(0.92))
                        .frame(width: localWidth)
                    Rectangle()
                        .fill(WayfinderTheme.cloud.opacity(0.80))
                }
                .clipShape(Capsule())
            }
        }
        .frame(height: 7)
        .accessibilityHidden(true)
    }
}
