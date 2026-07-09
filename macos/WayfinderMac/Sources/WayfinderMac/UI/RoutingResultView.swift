import SwiftUI

public struct RoutingResultView: View {
    private let decision: RoutingDecision

    public init(decision: RoutingDecision) {
        self.decision = decision
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(decision.route.displayName)
                        .font(.title3)
                        .fontWeight(.semibold)
                    Text(decision.selectedModel)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(decision.score.formatted(.number.precision(.fractionLength(2))))
                        .font(.system(.title2, design: .rounded, weight: .semibold))
                        .monospacedDigit()
                    Text("score")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            ProgressView(value: decision.score, total: 1)
                .tint(decision.route.tint)

            Text(decision.explanation)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            FeatureBreakdownView(features: decision.features, tint: decision.route.tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
    }
}

private extension RouteTarget {
    var tint: Color {
        switch self {
        case .local:
            return .green
        case .cloud:
            return .orange
        }
    }
}
