import SwiftUI

public struct ChatTurnHistoryRow: View {
    let turn: ChatTurn
    let isLast: Bool
    let canRetry: Bool
    let onRetry: () -> Void

    public init(
        turn: ChatTurn,
        isLast: Bool,
        canRetry: Bool,
        onRetry: @escaping () -> Void
    ) {
        self.turn = turn
        self.isLast = isLast
        self.canRetry = canRetry
        self.onRetry = onRetry
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            promptBubble

            if let response = turn.response {
                AssistantTurnResponse(
                    turn: turn,
                    response: response,
                    canRetry: canRetry,
                    onRetry: onRetry
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
                    .accessibilityLabel("You")
                    .accessibilityValue(turn.prompt.text)

                Text(turn.prompt.createdAt.formatted(date: .omitted, time: .shortened))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
                    .accessibilityLabel(
                        "Sent at \(turn.prompt.createdAt.formatted(date: .omitted, time: .shortened))"
                    )
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
    }
}

private struct AssistantTurnResponse: View {
    let turn: ChatTurn
    let response: ChatMessage
    let canRetry: Bool
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
                if !response.text.isEmpty {
                    Text(response.text)
                        .font(.body)
                        .foregroundStyle(response.state == .failed ? .secondary : .primary)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                        .accessibilityLabel("Wayfinder")
                        .accessibilityValue(response.text)
                        .accessibilityAddTraits(response.state == .streaming ? .updatesFrequently : [])
                }

                switch response.state {
                case .streaming:
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Responding")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Wayfinder is responding")
                    .accessibilityAddTraits(.updatesFrequently)
                case .failed:
                    HStack(spacing: 10) {
                        StatusStrip(title: "Chat failed", symbol: "exclamationmark.triangle.fill", color: .red)
                        if canRetry {
                            Button("Retry", action: onRetry)
                                .buttonStyle(.link)
                                .controlSize(.small)
                        }
                        Button("Open Settings") {
                            NotificationCenter.default.post(
                                name: .wayfinderOpenSettings,
                                object: response.recoverySettingsSection ?? SettingsSection.gateway
                            )
                        }
                        .buttonStyle(.link)
                        .controlSize(.small)
                    }
                case .stopped:
                    HStack(spacing: 10) {
                        StatusStrip(title: "Response stopped", symbol: "stop.circle", color: .secondary)
                        if canRetry {
                            Button("Retry", action: onRetry)
                                .buttonStyle(.link)
                                .controlSize(.small)
                        }
                    }
                case .complete:
                    EmptyView()
                }

                if let decision = response.decision {
                    RoutingReceiptButton(
                        turn: turn,
                        decision: decision
                    )
                }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

}

private struct RoutingReceiptButton: View {
    let turn: ChatTurn
    let decision: RoutingDecision
    @State private var showsRoutingDetails = false

    var body: some View {
        Button {
            showsRoutingDetails.toggle()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: decision.route.symbolName)
                    .foregroundStyle(decision.route.accentColor)
                Text(decision.routeSummary)
                    .fontWeight(.semibold)
                Image(systemName: "info.circle")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }
            .font(.caption)
            .padding(.vertical, 3)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(decision.route.accentColor)
        .accessibilityLabel("\(decision.routeSummary). Show routing details.")
        .help("Show routing details")
        .onHover { hovering in
            if hovering {
                showsRoutingDetails = true
            }
        }
        .popover(isPresented: $showsRoutingDetails, arrowEdge: .bottom) {
            RoutingOutputsPanel(
                turn: turn,
                onClose: { showsRoutingDetails = false }
            )
            .frame(width: 400, height: 560)
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
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Routing this message")
        .accessibilityAddTraits(.updatesFrequently)
    }
}
