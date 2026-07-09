import SwiftUI

public struct SavedSummarySection: View {
    let stats: RoutingStats

    public init(stats: RoutingStats) {
        self.stats = stats
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Label("Saved", systemImage: "chart.bar")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.primary)

                Spacer()

                Text(stats.savedToday.currencyText)
                    .font(.system(size: 13, weight: .semibold).monospacedDigit())
                    .foregroundStyle(.primary)
            }

            Text("Today: \(stats.savedToday.currencyText) · \(stats.percentVsAlwaysCloud.percentText) vs always-cloud")
                .font(.system(size: 12, weight: .regular))
                .foregroundStyle(.secondary)

            Text("Last 30 days: \(stats.savedLast30Days.currencyText)")
                .font(.system(size: 12, weight: .regular))
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
    }
}
