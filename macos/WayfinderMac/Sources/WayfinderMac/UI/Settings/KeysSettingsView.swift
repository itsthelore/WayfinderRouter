import SwiftUI

public struct KeysSettingsView: View {
    @Binding private var selectedProvider: ProviderKind

    public init(selectedProvider: Binding<ProviderKind>) {
        self._selectedProvider = selectedProvider
    }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Keys")
                        .font(.title3.weight(.semibold))
                    Text("API keys for provider routes. Account sign-in is managed separately.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                ProviderTabRow(selected: $selectedProvider)

                if selectedProvider == .openAI {
                    Label {
                        Text("ChatGPT subscription access is connected under Accounts and does not use an OpenAI API key.")
                    } icon: {
                        Image(systemName: "person.crop.circle")
                    }
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(WayfinderTheme.panel.opacity(0.46))
                    .accessibilityElement(children: .combine)
                }

                ProviderFormView(provider: selectedProvider)

                KeychainInfoBox()
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
