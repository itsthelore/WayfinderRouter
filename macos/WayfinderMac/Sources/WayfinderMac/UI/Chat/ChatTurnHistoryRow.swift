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
        VStack(alignment: .leading, spacing: 16) {
            promptBubble

            if let response = turn.response {
                AssistantTurnResponse(
                    response: response,
                    selectedDecisionID: selectedDecisionID,
                    onSelectDecision: onSelectDecision
                )
            } else {
                PendingRouteStrip()
            }
        }
        .padding(.bottom, isLast ? 2 : 8)
    }

    private var promptBubble: some View {
        HStack(alignment: .bottom, spacing: 10) {
            Spacer(minLength: 72)
            VStack(alignment: .trailing, spacing: 6) {
            Text(turn.prompt.text)
                    .font(.body)
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)

                Text(turn.prompt.createdAt.formatted(date: .omitted, time: .shortened))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
    }
}

private struct AssistantTurnResponse: View {
    let response: ChatMessage
    let selectedDecisionID: UUID?
    let onSelectDecision: (RoutingDecision) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                Circle()
                    .fill((response.decision?.route.accentColor ?? WayfinderTheme.local).opacity(0.13))
                Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(response.decision?.route.accentColor ?? WayfinderTheme.local)
            }
            .frame(width: 28, height: 28)
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 12) {
                if !response.text.isEmpty {
                    Text(response.text)
                        .font(.body)
                        .foregroundStyle(response.state == .failed ? .secondary : .primary)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }

                switch response.state {
                case .streaming:
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Responding")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                case .failed:
                    HStack(spacing: 10) {
                        StatusStrip(title: "Chat failed", symbol: "exclamationmark.triangle.fill", color: .red)
                        Button("Open Settings") {
                            NotificationCenter.default.post(name: .wayfinderOpenSettings, object: nil)
                        }
                        .buttonStyle(.link)
                        .controlSize(.small)
                    }
                case .stopped:
                    StatusStrip(title: "Response stopped", symbol: "stop.circle", color: .secondary)
                case .complete:
                    EmptyView()
                }

                if let decision = response.decision {
                    RoutingResponseCard(
                        decision: decision,
                        isSelected: decision.id == selectedDecisionID,
                        onSelect: { onSelectDecision(decision) }
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct StatusStrip: View {
    let title: String
    let symbol: String
    let color: Color

    var body: some View {
        Label(title, systemImage: symbol)
            .font(.caption.weight(.semibold))
            .foregroundStyle(color)
    }
}

private struct PendingRouteStrip: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(WayfinderTheme.local)
                .frame(width: 28, height: 28)
                .background(WayfinderTheme.local.opacity(0.13), in: Circle())
            ProgressView()
                .controlSize(.small)
            Text("Routing")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
