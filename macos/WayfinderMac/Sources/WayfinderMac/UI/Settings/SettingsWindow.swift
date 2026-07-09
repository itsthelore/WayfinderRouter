import SwiftUI

public struct WayfinderSettingsWindow: View {
    @EnvironmentObject private var appState: AppState

    public init() {}

    public var body: some View {
        HStack(spacing: 0) {
            SettingsSidebar(selected: $appState.selectedSettingsSection)
            Divider()
            settingsContent
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(.regularMaterial)
        }
        .frame(minWidth: 940, minHeight: 660)
    }

    @ViewBuilder
    private var settingsContent: some View {
        switch appState.selectedSettingsSection {
        case .gateway:
            GatewaySettingsView()
        case .routing:
            RoutingSettingsView()
        case .keys:
            KeysSettingsView()
        case .privacy:
            PrivacySettingsView()
        case .help:
            HelpSettingsView()
        default:
            PlaceholderSettingsView(section: appState.selectedSettingsSection)
        }
    }
}

private struct PlaceholderSettingsView: View {
    let section: SettingsSection

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(section.rawValue, systemImage: section.symbolName)
                .font(.title2.weight(.semibold))
            Text("This section will be wired after the native shell and Keys flow are settled.")
                .foregroundStyle(.secondary)
        }
        .padding(32)
    }
}
