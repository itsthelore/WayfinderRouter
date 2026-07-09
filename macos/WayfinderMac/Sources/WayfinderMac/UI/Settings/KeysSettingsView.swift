import SwiftUI

public struct KeysSettingsView: View {
    @EnvironmentObject private var appState: AppState

    public init() {}

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

                ProviderTabRow(selected: $appState.selectedProvider)

                ProviderFormView(provider: appState.selectedProvider)

                KeychainInfoBox()
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
