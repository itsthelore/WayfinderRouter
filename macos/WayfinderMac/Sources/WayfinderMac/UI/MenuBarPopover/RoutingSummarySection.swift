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
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.primary)

                Spacer()

                HStack(spacing: 10) {
                    RouteMetric(
                        symbolName: "desktopcomputer",
                        value: stats.localPercent.percentText,
                        color: WayfinderTheme.local
                    )
                    RouteMetric(
                        symbolName: "cloud",
                        value: stats.cloudPercent.percentText,
                        color: WayfinderTheme.cloud
                    )
                }
            }

            SplitRouteBar(localPercent: stats.localPercent)

            Text("Local \(stats.localPercent.percentText) · Cloud \(stats.cloudPercent.percentText)")
                .font(.system(size: 12, weight: .regular))
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Routing, local \(stats.localPercent.percentText), cloud \(stats.cloudPercent.percentText)")
    }
}

private struct RouteMetric: View {
    let symbolName: String
    let value: String
    let color: Color

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: symbolName)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(color)
            Text(value)
                .font(.system(size: 12, weight: .semibold).monospacedDigit())
                .foregroundStyle(.secondary)
        }
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
