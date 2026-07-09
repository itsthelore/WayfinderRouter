import SwiftUI

public struct SavedSummarySection: View {
    let stats: RoutingStats

    public init(stats: RoutingStats) {
        self.stats = stats
    }

    public var body: some View {
        HStack(spacing: NativeMenuMetrics.rowSpacing) {
            NativeMenuIconWell(symbolName: "chart.bar", tint: WayfinderTheme.cloud)

            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Saved")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(.primary)
                    Spacer()
                    Text(stats.hasSavings ? stats.savedToday.currencyText : "—")
                        .font(.system(size: 14, weight: .regular).monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                Text(stats.savedTodayDisplay)
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                Text(stats.savedLast30DaysDisplay)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .frame(height: NativeMenuMetrics.metricRowHeight)
        .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
        .accessibilityElement(children: .combine)
    }
}
