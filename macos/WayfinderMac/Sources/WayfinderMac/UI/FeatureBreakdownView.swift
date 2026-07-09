import SwiftUI

public struct FeatureBreakdownView: View {
    private let features: [RoutingFeature]
    private let tint: Color

    public init(features: [RoutingFeature], tint: Color = .accentColor) {
        self.features = features
        self.tint = tint
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Why")
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundStyle(.secondary)

            ForEach(features.prefix(6)) { feature in
                HStack(spacing: 8) {
                    Text(feature.label)
                        .font(.caption)
                        .lineLimit(1)
                        .frame(width: 128, alignment: .leading)
                        .foregroundStyle(.secondary)

                    ProgressView(value: feature.contribution ?? 0, total: 1)
                        .tint(tint)

                    Text(feature.value)
                        .font(.caption.monospacedDigit())
                        .frame(width: 34, alignment: .trailing)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
