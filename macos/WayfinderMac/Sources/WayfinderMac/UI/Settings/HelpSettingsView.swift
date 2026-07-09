import SwiftUI

public struct HelpSettingsView: View {
    public init() {}

    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Help")
                    .font(.title3.weight(.semibold))
                Text("Use one local Wayfinder gateway for many apps, then choose routing per app or request.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 0) {
                HelpRow(
                    title: "Connect apps",
                    value: "Use the Local Router, OpenAI-compatible, or Anthropic-compatible URL from Gateway settings.",
                    symbolName: "link"
                )
                HelpRow(
                    title: "Choose routing",
                    value: "Use model=\"auto\" to let Wayfinder choose from the configured routing policy.",
                    symbolName: "arrow.triangle.branch"
                )
                HelpRow(
                    title: "Pin local",
                    value: "Use model=\"prefer-local\" to prefer the local or lowest-cost configured tier.",
                    symbolName: "desktopcomputer"
                )
                HelpRow(
                    title: "Pin hosted",
                    value: "Use model=\"prefer-hosted\" to prefer the hosted or most-capable configured tier.",
                    symbolName: "cloud"
                )
                HelpRow(
                    title: "Pin a route",
                    value: "Use a configured endpoint name from Gateway's Available Routes row, such as model=\"ollama\" or model=\"fable-5\".",
                    symbolName: "mappin.and.ellipse"
                )
                HelpRow(
                    title: "Configured names only",
                    value: "Apps can select routes Wayfinder already knows about; they cannot invent new providers per request.",
                    symbolName: "checklist"
                )
                HelpRow(
                    title: "Diagnostics",
                    value: "Gateway settings contains Health Check, Config, Restart Gateway, and Refresh Status.",
                    symbolName: "stethoscope"
                )
                HelpRow(
                    title: "Privacy",
                    value: "Offline mode is the only mode that guarantees nothing leaves this Mac.",
                    symbolName: "wifi.slash"
                )
            }
            .background(WayfinderTheme.panel.opacity(0.62))
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(WayfinderTheme.hairline)
                    .frame(height: 1)
            }

            Spacer()
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 24)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct HelpRow: View {
    let title: String
    let value: String
    let symbolName: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: symbolName)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 20)

            Text(title)
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(width: 142, alignment: .leading)

            Text(value)
                .font(.callout)
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 12)
        }
        .frame(minHeight: 44)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
    }
}
