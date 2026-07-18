import SwiftUI

public struct RoutingOutputsPanel: View {
    let turn: ChatTurn?
    let onClose: () -> Void

    public init(turn: ChatTurn?, onClose: @escaping () -> Void = {}) {
        self.turn = turn
        self.onClose = onClose
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            InspectorHeader(subtitle: headerSubtitle, onClose: onClose)
                .padding(.horizontal, 18)
                .padding(.vertical, 15)

            Divider()
                .overlay(ChatWorkspaceChrome.border)

            if let turn {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        PromptInspectorPreview(turn: turn)
                            .padding(.horizontal, 18)
                            .padding(.vertical, 18)

                        InspectorDivider()

                        routingContent(for: turn)
                            .padding(18)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .id(turn.id)
            } else {
                EmptyRoutingInspector()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(ChatWorkspaceChrome.inspector)
    }

    @ViewBuilder
    private func routingContent(for turn: ChatTurn) -> some View {
        switch turn.routingInspectionState {
        case let .routed(decision):
            RoutedDecisionInspector(decision: decision)
        case .waiting:
            InspectorStatus(
                symbol: "point.topleft.down.curvedto.point.bottomright.up",
                tint: WayfinderTheme.local,
                title: "Choosing a route",
                message: "Wayfinder is evaluating this turn. The selected provider and routing signals will appear here."
            )
        case let .failed(message, decision):
            TerminalRoutingInspector(
                symbol: "exclamationmark.triangle.fill",
                tint: .red,
                title: "Reply failed",
                message: message.isEmpty ? "The gateway did not complete this reply." : message,
                actionTitle: "Open Settings",
                decision: decision
            ) {
                NotificationCenter.default.post(name: .wayfinderOpenSettings, object: nil)
            }
        case let .stopped(decision):
            TerminalRoutingInspector(
                symbol: "stop.circle",
                tint: ChatWorkspaceChrome.secondaryText,
                title: "Response stopped",
                message: decision == nil
                    ? "This turn ended before routing metadata was delivered."
                    : "The response stopped after Wayfinder selected a route.",
                decision: decision
            )
        case .unavailable:
            InspectorStatus(
                symbol: "questionmark.circle",
                tint: ChatWorkspaceChrome.secondaryText,
                title: "No routing metadata",
                message: "The gateway completed this turn without an inspectable routing decision."
            )
        }
    }

    private var headerSubtitle: String {
        guard let turn else {
            return "No turn selected"
        }

        switch turn.routingInspectionState {
        case let .routed(decision):
            return decision.routeSummary
        case .waiting:
            return "Routing in progress"
        case let .failed(_, decision):
            return decision.map { "Reply failed · \($0.routeSummary)" } ?? "Reply failed"
        case let .stopped(decision):
            return decision.map { "Stopped · \($0.routeSummary)" } ?? "Response stopped"
        case .unavailable:
            return "Metadata unavailable"
        }
    }
}

private struct InspectorHeader: View {
    let subtitle: String
    let onClose: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "sidebar.right")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("Routing")
                    .font(.headline.weight(.semibold))
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            }
            Spacer()
            Button(action: onClose) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .controlSize(.small)
            .accessibilityLabel("Close routing inspector")
            .help("Close routing inspector")
        }
    }
}

private struct PromptInspectorPreview: View {
    let turn: ChatTurn

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            InspectorEyebrow("Prompt")
            HStack(alignment: .top, spacing: 10) {
                Rectangle()
                    .fill(ChatWorkspaceChrome.border)
                    .frame(width: 2)
                Text(turn.prompt.text)
                    .font(.callout)
                    .foregroundStyle(.primary.opacity(0.9))
                    .lineLimit(6)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
    }
}

private struct RoutedDecisionInspector: View {
    let decision: RoutingDecision

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            DecisionSummary(decision: decision)

            InspectorDivider()

            VStack(alignment: .leading, spacing: 10) {
                InspectorEyebrow("Destination")
                InspectorValueRow(label: "Provider", value: decision.provider)
                InspectorValueRow(label: "Mode", value: decision.mode)
            }

            InspectorDivider()

            VStack(alignment: .leading, spacing: 9) {
                InspectorEyebrow("Why")
                Text(decision.explanation)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            InspectorDivider()

            DecisionScoreSection(decision: decision)

            if !decision.features.isEmpty {
                InspectorDivider()
                DecisionSignalsSection(decision: decision)
            }
        }
    }
}

private struct TerminalRoutingInspector: View {
    let symbol: String
    let tint: Color
    let title: String
    let message: String
    let actionTitle: String?
    let decision: RoutingDecision?
    let action: (() -> Void)?

    init(
        symbol: String,
        tint: Color,
        title: String,
        message: String,
        actionTitle: String? = nil,
        decision: RoutingDecision?,
        action: (() -> Void)? = nil
    ) {
        self.symbol = symbol
        self.tint = tint
        self.title = title
        self.message = message
        self.actionTitle = actionTitle
        self.decision = decision
        self.action = action
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            InspectorStatus(
                symbol: symbol,
                tint: tint,
                title: title,
                message: message,
                actionTitle: actionTitle,
                action: action
            )

            if let decision {
                InspectorDivider()
                RoutedDecisionInspector(decision: decision)
            }
        }
    }
}

private struct DecisionSummary: View {
    let decision: RoutingDecision

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                Circle()
                    .fill(decision.route.accentColor.opacity(0.14))
                Image(systemName: decision.route.symbolName)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(decision.route.accentColor)
            }
            .frame(width: 36, height: 36)
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(decision.routeSummary)
                    .font(.title3.weight(.semibold))
            }

            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct InspectorValueRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            Spacer(minLength: 12)
            Text(value)
                .fontWeight(.medium)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .font(.callout)
        .accessibilityElement(children: .combine)
    }
}

private struct DecisionScoreSection: View {
    let decision: RoutingDecision

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(alignment: .firstTextBaseline) {
                InspectorEyebrow("Routing score")
                Spacer()
                Text(decision.score.scoreText)
                    .font(.title3.monospacedDigit().weight(.semibold))
                    .foregroundStyle(decision.route.accentColor)
            }

            ScoreMeter(decision: decision)

            HStack {
                Text("Local")
                Spacer()
                Text("Cloud")
            }
            .font(.caption2)
            .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Routing score \(decision.score.scoreText)")
    }
}

private struct ScoreMeter: View {
    let decision: RoutingDecision

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.primary.opacity(0.10))

                Capsule()
                    .fill(decision.route.accentColor)
                    .frame(width: max(8, proxy.size.width * min(max(decision.score, 0), 1)))
            }
        }
        .frame(height: 6)
    }
}

private struct DecisionSignalsSection: View {
    let decision: RoutingDecision
    @State private var isExpanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: 11) {
                ForEach(decision.features.prefix(6)) { feature in
                    FeatureSignalRow(feature: feature, tint: decision.route.accentColor)
                }
            }
            .padding(.top, 11)
        } label: {
            InspectorEyebrow("Signals")
        }
        .disclosureGroupStyle(.automatic)
    }
}

private struct FeatureSignalRow: View {
    let feature: RoutingFeature
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(feature.label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer()
                Text(feature.value)
                    .font(.caption.monospacedDigit().weight(.medium))
                    .foregroundStyle(.primary.opacity(0.72))
                    .lineLimit(1)
            }

            if let contribution = feature.contribution {
                GeometryReader { proxy in
                    ZStack(alignment: .leading) {
                        Capsule()
                            .fill(Color.primary.opacity(0.09))
                        Capsule()
                            .fill(tint.opacity(0.82))
                            .frame(width: max(4, proxy.size.width * min(max(contribution, 0), 1)))
                    }
                }
                .frame(height: 3)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct InspectorStatus: View {
    let symbol: String
    let tint: Color
    let title: String
    let message: String
    let actionTitle: String?
    let action: (() -> Void)?

    init(
        symbol: String,
        tint: Color,
        title: String,
        message: String,
        actionTitle: String? = nil,
        action: (() -> Void)? = nil
    ) {
        self.symbol = symbol
        self.tint = tint
        self.title = title
        self.message = message
        self.actionTitle = actionTitle
        self.action = action
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(tint)
                .accessibilityHidden(true)
            Text(title)
                .font(.headline)
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct EmptyRoutingInspector: View {
    var body: some View {
        VStack(spacing: 10) {
            Spacer()
            Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                .font(.title2)
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                .accessibilityHidden(true)
            Text("Select a turn")
                .font(.headline)
            Text("Its destination, score, and routing signals will appear here.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct InspectorDivider: View {
    var body: some View {
        Divider()
            .overlay(ChatWorkspaceChrome.border)
    }
}

private struct InspectorEyebrow: View {
    let title: String

    init(_ title: String) {
        self.title = title
    }

    var body: some View {
        Text(title)
            .font(.caption2.weight(.semibold))
            .textCase(.uppercase)
            .tracking(0.7)
            .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
    }
}
