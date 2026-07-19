import SwiftUI

public final class SettingsWindowNavigation: ObservableObject {
    @Published public var selectedSection: SettingsSection

    public init(selectedSection: SettingsSection = .gateway) {
        self.selectedSection = selectedSection
    }

    public func select(_ section: SettingsSection) {
        selectedSection = section
    }

    public static func section(from notification: Notification) -> SettingsSection {
        notification.object as? SettingsSection ?? .gateway
    }
}

public struct WayfinderSettingsWindow: View {
    @ObservedObject private var navigation: SettingsWindowNavigation
    @ObservedObject private var appState: AppState
    @State private var selectedProvider: ProviderKind = .anthropic
    @StateObject private var codexAccountState: CodexAccountSettingsState

    public init(
        appState: AppState,
        accountClient: any CodexAccountClient = GatewayCodexAccountClient(),
        navigation: SettingsWindowNavigation = SettingsWindowNavigation()
    ) {
        self.navigation = navigation
        self.appState = appState
        _codexAccountState = StateObject(
            wrappedValue: CodexAccountSettingsState(
                client: accountClient,
                onAccountStateChanged: { appState.refreshStats() }
            )
        )
    }

    public var body: some View {
        NavigationSplitView {
            SettingsSidebar(selected: $navigation.selectedSection)
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
        switch navigation.selectedSection {
        case .gateway:
            GatewaySettingsView()
        case .routing:
            RoutingSettingsView(appState: appState)
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
