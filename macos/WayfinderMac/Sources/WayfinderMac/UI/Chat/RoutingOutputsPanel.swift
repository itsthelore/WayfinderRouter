import SwiftUI

public struct RoutingOutputsPanel: View {
    let decision: RoutingDecision?
    let turn: ChatTurn?
    let onClose: () -> Void

    public init(decision: RoutingDecision?, turn: ChatTurn?, onClose: @escaping () -> Void = {}) {
        self.decision = decision
        self.turn = turn
        self.onClose = onClose
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            InspectorHeader(onClose: onClose)
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 16)

            Divider()
                .overlay(ChatWorkspaceChrome.border)

            if let decision {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        if let turn {
                            PromptInspectorPreview(turn: turn)
                        }

                        DecisionHero(decision: decision)
                        DecisionWhySection(decision: decision)
                        DecisionScoreSection(decision: decision)
                        DecisionSignalsSection(decision: decision)
                    }
                    .padding(20)
                }
            } else {
                EmptyOutputsPanel()
                    .padding(24)
            }

            Spacer(minLength: 0)
        }
        .frame(width: ChatWorkspaceChrome.inspectorWidth)
        .background(ChatWorkspaceChrome.panel)
    }
}

private struct InspectorHeader: View {
    let onClose: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "sidebar.right")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            VStack(alignment: .leading, spacing: 2) {
                Text("Route")
                    .font(.headline.weight(.semibold))
                Text("Selected decision")
                    .font(.caption)
                    .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            }
            Spacer()
            Button(action: onClose) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .controlSize(.small)
            .help("Close route details")
        }
    }
}

private struct PromptInspectorPreview: View {
    let turn: ChatTurn

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            InspectorEyebrow("Prompt")
            Text(turn.prompt.text)
                .font(.callout.weight(.medium))
                .foregroundStyle(.primary.opacity(0.9))
                .lineLimit(5)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(ChatWorkspaceChrome.border, lineWidth: 1)
        )
    }
}

private struct DecisionHero: View {
    let decision: RoutingDecision

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(decision.route.accentColor.opacity(0.14))
                    Image(systemName: decision.route.symbolName)
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(decision.route.accentColor)
                }
                .frame(width: 42, height: 42)

                VStack(alignment: .leading, spacing: 3) {
                    Text(decision.routeSummary)
                        .font(.title3.weight(.semibold))
                    Text(decision.routeReasonTitle)
                        .font(.caption)
                        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                }

                Spacer()
            }

            HStack(spacing: 10) {
                DecisionPill(title: "Provider", value: decision.provider)
                DecisionPill(title: "Mode", value: decision.mode)
            }
        }
    }
}

private struct DecisionPill: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            Text(value)
                .font(.caption.weight(.semibold))
                .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
    }
}

private struct DecisionWhySection: View {
    let decision: RoutingDecision

    var body: some View {
        InspectorSection(title: "Why", symbol: "text.justify.leading") {
            Text(decision.explanation)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct DecisionScoreSection: View {
    let decision: RoutingDecision

    var body: some View {
        InspectorSection(title: "Score", symbol: "gauge.with.dots.needle.67percent") {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline) {
                    Text(decision.score.scoreText)
                        .font(.system(size: 34, weight: .semibold, design: .rounded).monospacedDigit())
                        .foregroundStyle(decision.route.accentColor)
                    Spacer()
                    Text(decision.routeSummary)
                        .font(.caption.monospacedDigit().weight(.semibold))
                        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(ChatWorkspaceChrome.mutedFill, in: Capsule())
                }

                ScoreMeter(decision: decision)

                HStack {
                    Text("0")
                    Spacer()
                    Text("Routing score")
                    Spacer()
                    Text("1")
                }
                .font(.caption2.monospacedDigit())
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }
        }
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
        .frame(height: 8)
    }
}

private struct DecisionSignalsSection: View {
    let decision: RoutingDecision

    var body: some View {
        InspectorSection(title: "Signals", symbol: "list.bullet.rectangle") {
            VStack(spacing: 8) {
                ForEach(decision.features.prefix(6)) { feature in
                    FeatureSignalRow(feature: feature, tint: decision.route.accentColor)
                }
            }
        }
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
                .frame(height: 4)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct EmptyOutputsPanel: View {
    var body: some View {
        VStack(alignment: .center, spacing: 10) {
            Spacer()
            Image(systemName: "sidebar.right")
                .font(.title2)
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            Text("Select a route")
                .font(.headline)
            Text("Decision details, score, and feature signals will appear here.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct InspectorSection<Content: View>: View {
    let title: String
    let symbol: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 7) {
                Image(systemName: symbol)
                    .frame(width: 14)
                InspectorEyebrow(title)
            }
            .foregroundStyle(ChatWorkspaceChrome.secondaryText)

            content
        }
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
