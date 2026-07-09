import SwiftUI

struct ModelStatusSection: View {
    let stats: RoutingStats

    var body: some View {
        VStack(spacing: 0) {
            ModelStatusRow(
                symbolName: "server.rack",
                name: "Gateway",
                detail: stats.isRunning ? "Local router ready" : "Service stopped",
                status: stats.isRunning ? "Ready" : "Offline",
                color: stats.isRunning ? WayfinderTheme.local : Color.secondary.opacity(0.55)
            )

            Divider()
                .opacity(0.35)

            ModelStatusRow(
                symbolName: "cloud",
                name: "Hosted models",
                detail: "Provider keys in Settings",
                status: stats.isRunning ? "Check keys" : "Unavailable",
                color: WayfinderTheme.cloud
            )
        }
        .padding(.vertical, 4)
    }
}

private struct ModelStatusRow: View {
    let symbolName: String
    let name: String
    let detail: String
    let status: String
    let color: Color

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbolName)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(color)
                .frame(width: 16)

            VStack(alignment: .leading, spacing: 1) {
                Text(name)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)
                Text(detail)
                    .font(.system(size: 11, weight: .regular))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Text(status)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
        }
        .frame(height: 38)
        .accessibilityElement(children: .combine)
    }
}
