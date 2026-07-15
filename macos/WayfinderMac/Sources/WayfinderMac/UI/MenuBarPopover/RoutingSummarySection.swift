import SwiftUI

public struct RoutingSummarySection: View {
    private let presentation: RoutingPopoverPresentation

    public init(stats: RoutingStats) {
        self.presentation = RoutingPopoverPresentation(stats: stats)
    }

    init(presentation: RoutingPopoverPresentation) {
        self.presentation = presentation
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("Routing")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)

                Spacer(minLength: 8)

                if let totalText = presentation.totalText {
                    Text(totalText)
                        .font(.system(size: 12, weight: .regular).monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }

            SplitRouteBar(
                localFraction: presentation.localFraction,
                hasDecisions: presentation.hasDecisions
            )
            .frame(height: 6)

            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(presentation.localText)
                Spacer(minLength: 6)
                if !presentation.cloudText.isEmpty {
                    Text(presentation.cloudText)
                }
            }
            .font(.system(size: 11, weight: .regular).monospacedDigit())
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
        .frame(height: NativeMenuMetrics.routingRowHeight)
        .padding(.horizontal, NativeMenuMetrics.sectionHorizontalPadding)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(presentation.accessibilityLabel)
        .accessibilityAddTraits(.isStaticText)
    }
}

private struct SplitRouteBar: View {
    let localFraction: Double
    let hasDecisions: Bool

    var body: some View {
        GeometryReader { proxy in
            let fraction = min(1, max(0, localFraction))
            let localWidth = proxy.size.width * CGFloat(fraction)

            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.primary.opacity(0.12))

                if hasDecisions {
                    HStack(spacing: 0) {
                        Rectangle()
                            .fill(WayfinderTheme.local)
                            .frame(width: localWidth)
                        Rectangle()
                            .fill(WayfinderTheme.cloud)
                    }
                    .clipShape(Capsule())
                }
            }
        }
        .accessibilityHidden(true)
    }
}
