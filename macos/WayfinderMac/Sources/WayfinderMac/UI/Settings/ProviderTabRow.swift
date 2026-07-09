import SwiftUI

public struct ProviderTabRow: View {
    @Binding var selected: ProviderKind

    public init(selected: Binding<ProviderKind>) {
        self._selected = selected
    }

    public var body: some View {
        Picker("Provider", selection: $selected) {
            ForEach(ProviderKind.allCases) { provider in
                Text(provider.rawValue)
                    .tag(provider)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }
}
