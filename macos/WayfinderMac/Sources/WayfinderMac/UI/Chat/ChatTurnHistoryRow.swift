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
                    AssistantTurnResponse(
                        response: response,
                        selectedDecisionID: selectedDecisionID,
                        onSelectDecision: onSelectDecision
                    )
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

private struct AssistantTurnResponse: View {
    let response: ChatMessage
    let selectedDecisionID: UUID?
    let onSelectDecision: (RoutingDecision) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
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
                    Spacer(minLength: 12)
                    Button("Open Settings") {
                        NotificationCenter.default.post(name: .wayfinderOpenSettings, object: nil)
                    }
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
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(ChatWorkspaceChrome.panel, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(response.state == .failed ? Color.red.opacity(0.22) : ChatWorkspaceChrome.border, lineWidth: 1)
        )
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
