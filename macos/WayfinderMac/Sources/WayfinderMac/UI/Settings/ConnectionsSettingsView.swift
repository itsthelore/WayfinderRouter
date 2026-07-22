import SwiftUI

public enum ConnectionKind: String, CaseIterable, Identifiable, Sendable {
    case chatGPT = "ChatGPT"
    case anthropic = "Anthropic API"
    case openAI = "OpenAI API"
    case googleGemini = "Google Gemini API"
    case ollama = "Ollama"
    case lmStudio = "LM Studio"
    case custom = "Custom API"

    public var id: String { rawValue }

    var provider: ProviderKind? {
        switch self {
        case .chatGPT: nil
        case .anthropic: .anthropic
        case .openAI: .openAI
        case .googleGemini: .googleGemini
        case .ollama: .ollama
        case .lmStudio: .lmStudio
        case .custom: .custom
        }
    }
}

public struct ConnectionsSettingsView: View {
    @Binding private var selectedConnection: ConnectionKind
    @ObservedObject private var accountState: CodexAccountSettingsState

    public init(
        selectedConnection: Binding<ConnectionKind>,
        accountState: CodexAccountSettingsState
    ) {
        self._selectedConnection = selectedConnection
        self.accountState = accountState
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Connections")
                        .font(.title3.weight(.semibold))
                    Text("Connect accounts, APIs, and local model servers that Wayfinder can use.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                HStack {
                    Text("Service")
                        .font(.callout.weight(.medium))
                    Spacer()
                    Picker("Service", selection: $selectedConnection) {
                        ForEach(ConnectionKind.allCases) { connection in
                            Text(connection.rawValue).tag(connection)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 230)
                }

                Divider()
            }
            .padding(.horizontal, 28)
            .padding(.top, 24)

            if selectedConnection == .chatGPT {
                AccountsSettingsView(accountState: accountState, embedded: true)
                    .padding(.horizontal, 28)
                    .padding(.bottom, 16)
            } else if let provider = selectedConnection.provider {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                    ProviderFormView(provider: provider)
                    KeychainInfoBox()
                    }
                    .padding(.horizontal, 28)
                    .padding(.bottom, 24)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
