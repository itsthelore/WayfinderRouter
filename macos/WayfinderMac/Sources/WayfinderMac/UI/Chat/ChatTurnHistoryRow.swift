import SwiftUI

public struct ChatTurnHistoryRow: View {
    let turn: ChatTurn
    let isLast: Bool
    let selectedDecisionID: UUID?
    let onSelectDecision: (RoutingDecision) -> Void

    public init(
        turn: ChatTurn,
        isLast: Bool,
        selectedDecisionID: UUID?,
        onSelectDecision: @escaping (RoutingDecision) -> Void
    ) {
        self.turn = turn
        self.isLast = isLast
        self.selectedDecisionID = selectedDecisionID
        self.onSelectDecision = onSelectDecision
    }

    public var body: some View {
        HStack(alignment: .top, spacing: 12) {
            timelineMarker
                .padding(.top, 4)

            VStack(alignment: .leading, spacing: 8) {
                promptHeader

                if let response = turn.response {
                    if let decision = response.decision {
                        RoutingResponseCard(
                            decision: decision,
                            isSelected: decision.id == selectedDecisionID,
                            onSelect: { onSelectDecision(decision) }
                        )
                    } else {
                        FailedRouteStrip(message: response.text)
                    }
                } else {
                    PendingRouteStrip()
                }
            }
            .padding(.bottom, isLast ? 4 : 18)
        }
    }

    private var promptHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("Prompt")
                    .font(.caption2.weight(.semibold))
                    .textCase(.uppercase)
                    .tracking(0.7)
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)

                Spacer(minLength: 14)

                Text(turn.prompt.createdAt.formatted(date: .omitted, time: .shortened))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }

            Text(turn.prompt.text)
                .font(.callout.weight(.semibold))
                .foregroundStyle(.primary)
                .lineLimit(4)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 2)
    }

    private var timelineMarker: some View {
        VStack(spacing: 6) {
            ZStack {
                Circle()
                    .fill((turn.response?.decision?.route.accentColor ?? Color.secondary).opacity(0.12))
                    .frame(width: 18, height: 18)
                Circle()
                    .fill(turn.response?.decision?.route.accentColor ?? Color.secondary.opacity(0.45))
                    .frame(width: 7, height: 7)
            }

            if !isLast {
                Rectangle()
                    .fill(WayfinderTheme.hairline)
                    .frame(width: 1)
                    .frame(maxHeight: .infinity)
            }
        }
        .frame(width: 24)
        .frame(minHeight: 118)
    }
}

private struct FailedRouteStrip: View {
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Routing failed", systemImage: "exclamationmark.triangle.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.red)
            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.07), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Color.red.opacity(0.22), lineWidth: 1)
        )
    }
}

private struct PendingRouteStrip: View {
    var body: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)
            Text("Routing")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WayfinderTheme.panel, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}
