import SwiftUI

public struct RouteWhyDisclosure: View {
    let decision: RoutingDecision

    public init(decision: RoutingDecision) {
        self.decision = decision
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Divider()
                .padding(.bottom, 1)

            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 10) {
                    Text("Routing score")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(decision.score.scoreText)
                        .font(.caption.monospacedDigit().weight(.semibold))
                        .foregroundStyle(decision.route.accentColor)
                }

                ProgressView(value: decision.score, total: 1)
                    .tint(decision.route.accentColor)

                HStack {
                    Text("0")
                    Spacer()
                    Text("1")
                }
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.tertiary)
            }

            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "checkmark.seal")
                    .foregroundStyle(decision.route.accentColor)
                    .font(.caption)
                Text(decision.explanation)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(10)
            .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 9, style: .continuous))

            HStack {
                Text("Signals")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Text(decision.mode)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Color.primary.opacity(0.05), in: Capsule())
            }

            ForEach(whyRows) { row in
                HStack(spacing: 8) {
                    Circle()
                        .fill(decision.route.accentColor.opacity(0.72))
                        .frame(width: 5, height: 5)
                    Text(row.title)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(row.value)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var whyRows: [WhyRow] {
        let featureRows = decision.features.prefix(4).map {
            WhyRow(title: $0.label, value: $0.value)
        }
        let threshold = decision.route == .local ? "Below local threshold" : "Above cloud threshold"
        return Array(featureRows) + [WhyRow(title: threshold, value: decision.score.scoreText)]
    }
}

private struct WhyRow: Identifiable {
    let id = UUID()
    let title: String
    let value: String
}
