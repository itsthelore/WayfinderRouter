import SwiftUI

public struct PrivacySettingsView: View {
    public init() {}

    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Privacy")
                    .font(.title3.weight(.semibold))
                Text("What the app and gateway do with prompts and keys.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 0) {
                SettingsInfoRow(
                    title: "Routing",
                    value: "The routing decision is computed on your machine.",
                    symbolName: "point.topleft.down.curvedto.point.bottomright.up"
                )
                SettingsInfoRow(
                    title: "Prompts",
                    value: "Prompts go only to the provider selected by the local gateway.",
                    symbolName: "arrow.triangle.branch"
                )
                SettingsInfoRow(
                    title: "Keys",
                    value: "Provider keys live in the macOS Keychain, not in app settings.",
                    symbolName: "key"
                )
                SettingsInfoRow(
                    title: "Offline",
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
