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
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .center, spacing: 12) {
                    RouteBadge(decision: decision)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(decision.routeReasonTitle)
                            .font(.callout.weight(.semibold))
                            .foregroundStyle(.primary)
                            .lineLimit(1)
                        Text("\(decision.provider) · \(decision.mode)")
                            .font(.caption)
                            .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 14)

                    Text(decision.score.scoreText)
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(decision.route.accentColor)

                    Image(systemName: isSelected ? "sidebar.right" : "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(isSelected ? decision.route.accentColor : ChatWorkspaceChrome.tertiaryText)
                }

                ScoreRail(decision: decision)

                Text(decision.explanation)
                    .font(.caption)
                    .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                isSelected ? ChatWorkspaceChrome.panelRaised : ChatWorkspaceChrome.panel,
                in: RoundedRectangle(cornerRadius: 10, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(isSelected ? decision.route.accentColor.opacity(0.52) : ChatWorkspaceChrome.border, lineWidth: 1)
            )
            .overlay(alignment: .leading) {
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(decision.route.accentColor)
                    .frame(width: 3)
                    .padding(.vertical, 12)
            }
        }
        .buttonStyle(.plain)
    }
}

private struct RouteBadge: View {
    let decision: RoutingDecision

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: decision.route.symbolName)
                .font(.system(size: 12, weight: .semibold))
            Text(decision.route.label)
                .font(.caption.weight(.bold))
        }
        .foregroundStyle(decision.route.accentColor)
        .frame(minWidth: 82)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(decision.route.accentColor.opacity(0.13), in: Capsule())
    }
}

private struct ScoreRail: View {
    let decision: RoutingDecision

    var body: some View {
        HStack(spacing: 8) {
            Text("score")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.primary.opacity(0.10))
                    Capsule()
                        .fill(decision.route.accentColor)
                        .frame(width: max(6, proxy.size.width * min(max(decision.score, 0), 1)))
                }
            }
            .frame(height: 6)
        }
    }
}
