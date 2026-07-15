import SwiftUI

public struct SettingsSidebar: View {
    @Binding var selected: SettingsSection

    public init(selected: Binding<SettingsSection>) {
        self._selected = selected
    }

    public var body: some View {
        List(selection: $selected) {
            ForEach(SettingsSection.allCases) { section in
                Label(section.rawValue, systemImage: section.symbolName)
                    .tag(section)
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("Settings")
    }
}
