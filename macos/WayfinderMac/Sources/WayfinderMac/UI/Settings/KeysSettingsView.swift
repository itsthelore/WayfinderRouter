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
                    Text("Provider credentials are read by the gateway.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                ProviderTabRow(selected: $selectedProvider)

                ProviderFormView(provider: selectedProvider)

                KeychainInfoBox()
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
