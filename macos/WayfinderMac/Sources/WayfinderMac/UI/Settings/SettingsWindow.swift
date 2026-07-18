import SwiftUI

public struct WayfinderSettingsWindow: View {
    @State private var selectedSection: SettingsSection = .gateway
    @State private var selectedProvider: ProviderKind = .anthropic
    @StateObject private var codexAccountState: CodexAccountSettingsState

    public init(accountClient: any CodexAccountClient = GatewayCodexAccountClient()) {
        _codexAccountState = StateObject(
            wrappedValue: CodexAccountSettingsState(client: accountClient)
        )
    }

    public var body: some View {
        NavigationSplitView {
            SettingsSidebar(selected: $selectedSection)
                .navigationSplitViewColumnWidth(min: 170, ideal: 180, max: 190)
        } detail: {
            settingsContent
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(.regularMaterial)
        }
        .navigationSplitViewStyle(.balanced)
        .frame(minWidth: 620, minHeight: 460)
    }

    @ViewBuilder
    private var settingsContent: some View {
        switch selectedSection {
        case .gateway:
            GatewaySettingsView()
        case .routing:
            RoutingSettingsView()
        case .accounts:
            AccountsSettingsView(accountState: codexAccountState)
        case .keys:
            KeysSettingsView(selectedProvider: $selectedProvider)
        case .privacy:
            PrivacySettingsView()
        case .help:
            HelpSettingsView()
        case .about:
            AboutSettingsView()
        }
    }
}

private struct AboutSettingsView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Wayfinder")
                .font(.title3.weight(.semibold))
            Text("A native menu-bar client for the local Wayfinder gateway.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
