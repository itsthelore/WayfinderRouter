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
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.primary)

                Spacer()

                Text(stats.hasSavings ? stats.savedToday.currencyText : "—")
                    .font(.system(size: 13, weight: .regular).monospacedDigit())
                    .foregroundStyle(.primary)
            }

            Text(stats.savedTodayDisplay)
                .font(.system(size: 14, weight: .regular))
                .foregroundStyle(.primary)

            Text(stats.savedLast30DaysDisplay)
                .font(.system(size: 14, weight: .regular))
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }
}
