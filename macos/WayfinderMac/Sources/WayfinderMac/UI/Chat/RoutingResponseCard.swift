import SwiftUI

public struct RoutingResponseCard: View {
    let decision: RoutingDecision
    let isSelected: Bool
    let onSelect: () -> Void

    public init(
        decision: RoutingDecision,
        isSelected: Bool = false,
        onSelect: @escaping () -> Void = {}
    ) {
        self.decision = decision
        self.isSelected = isSelected
        self.onSelect = onSelect
    }

    public var body: some View {
        Button(action: onSelect) {
            HStack(spacing: 10) {
                ZStack {
                    Circle()
                        .fill(decision.route.accentColor.opacity(0.13))
                    Image(systemName: decision.route.symbolName)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(decision.route.accentColor)
                }
                .frame(width: 26, height: 26)

                VStack(alignment: .leading, spacing: 2) {
                    Text(decision.routeSummary)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.primary)
                    Text("\(decision.provider) · \(decision.mode)")
                    .font(.caption)
                    .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                        .lineLimit(1)
                }

                Spacer(minLength: 12)

                Text(decision.score.scoreText)
                    .font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(decision.route.accentColor)

                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                isSelected ? ChatWorkspaceChrome.selectedFill : ChatWorkspaceChrome.mutedFill,
                in: RoundedRectangle(cornerRadius: 9, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .stroke(isSelected ? decision.route.accentColor.opacity(0.42) : ChatWorkspaceChrome.border, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(decision.routeSummary), \(decision.provider), score \(decision.score.scoreText)")
        .accessibilityHint("Shows the routing decision details.")
    }
}
