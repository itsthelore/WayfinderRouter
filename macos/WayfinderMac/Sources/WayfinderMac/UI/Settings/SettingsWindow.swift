import SwiftUI

public final class SettingsWindowNavigation: ObservableObject {
    @Published public var selectedSection: SettingsSection

    public init(selectedSection: SettingsSection = .connections) {
        self.selectedSection = selectedSection
    }

    public func select(_ section: SettingsSection) {
        selectedSection = section
    }

    public static func section(from notification: Notification) -> SettingsSection {
        notification.object as? SettingsSection ?? .connections
    }
}

public struct WayfinderSettingsWindow: View {
    @ObservedObject private var navigation: SettingsWindowNavigation
    @ObservedObject private var appState: AppState
    @State private var selectedConnection: ConnectionKind = .chatGPT
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
        case .connections:
            ConnectionsSettingsView(
                selectedConnection: $selectedConnection,
                accountState: codexAccountState
            )
        case .about:
            AboutSettingsView()
        }
    }
}

public struct AboutSettingsView: View {
    @State private var showsPrivacy = false
    @State private var showsHelp = false

    public init() {}

    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Wayfinder")
                    .font(.title3.weight(.semibold))
                Text("Local-first model routing and chat for this Mac.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 0) {
                SettingsInfoRow(
                    title: "Version",
                    value: versionLabel,
                    symbolName: "app.badge"
                )
                SettingsInfoRow(
                    title: "Gateway",
                    value: "Runs locally and chooses from your configured connections.",
                    symbolName: "server.rack"
                )
            }
            .background(WayfinderTheme.panel.opacity(0.62))

            DisclosureGroup("Privacy", isExpanded: $showsPrivacy) {
                VStack(spacing: 0) {
                    SettingsInfoRow(title: "Routing", value: "Routing decisions are computed on this Mac.", symbolName: "point.topleft.down.curvedto.point.bottomright.up")
                    SettingsInfoRow(title: "Prompts", value: "Prompts go only to the connection selected by the gateway.", symbolName: "arrow.triangle.branch")
                    SettingsInfoRow(title: "Credentials", value: "API keys are stored in the macOS Keychain.", symbolName: "key")
                    SettingsInfoRow(title: "Offline", value: "Offline mode is the only mode that guarantees nothing leaves this Mac.", symbolName: "wifi.slash")
                }
                .padding(.top, 8)
            }

            DisclosureGroup("Help", isExpanded: $showsHelp) {
                VStack(spacing: 0) {
                    SettingsInfoRow(title: "Connect apps", value: "Copy compatible URLs from Gateway → Integration details.", symbolName: "link")
                    SettingsInfoRow(title: "Automatic", value: "Use the auto model route to let Wayfinder choose.", symbolName: "arrow.triangle.branch")
                    SettingsInfoRow(title: "Pinned routes", value: "Choose prefer-local, prefer-hosted, or a named connection when needed.", symbolName: "mappin.and.ellipse")
                    SettingsInfoRow(title: "Diagnostics", value: "Gateway contains restart, status and configuration tools.", symbolName: "stethoscope")
                }
                .padding(.top, 8)
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var versionLabel: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        return build.map { "\(version) (\($0))" } ?? version
    }
}
