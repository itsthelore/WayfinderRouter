import SwiftUI

public struct SettingsSidebar: View {
    @Binding var selected: SettingsSection

    public init(selected: Binding<SettingsSection>) {
        self._selected = selected
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Settings")
                .font(.headline.weight(.semibold))
                .padding(.bottom, 8)

            ForEach(SettingsSection.allCases) { section in
                Button {
                    selected = section
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: section.symbolName)
                            .font(.system(size: 13, weight: .medium))
                            .frame(width: 18, height: 18)
                        Text(section.rawValue)
                            .font(.callout)
                        Spacer()
                        if !section.isAvailableInNativePrototype {
                            Text("Coming Soon")
                                .font(.caption2.weight(.medium))
                                .foregroundStyle(.secondary)
                        }
                    }
                    .frame(height: 34)
                    .padding(.horizontal, 10)
                    .contentShape(Rectangle())
                    .background(
                        selected == section ? section.selectionTint : Color.clear,
                        in: RoundedRectangle(cornerRadius: 7, style: .continuous)
                    )
                    .foregroundStyle(rowForeground(for: section))
                }
                .buttonStyle(.plain)
                .disabled(!section.isAvailableInNativePrototype)
                .help(section.isAvailableInNativePrototype ? "" : "This settings section is not wired in the native prototype yet.")
            }

            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 18)
        .frame(width: 206)
        .background(.bar)
    }

    private func rowForeground(for section: SettingsSection) -> Color {
        if !section.isAvailableInNativePrototype {
            return .secondary
        }
        return selected == section ? section.selectionAccent : .primary
    }
}

private extension SettingsSection {
    var isAvailableInNativePrototype: Bool {
        self == .gateway || self == .routing || self == .keys || self == .privacy || self == .help
    }

    var selectionAccent: Color {
        switch self {
        case .gateway, .routing, .keys, .privacy, .help:
            return WayfinderTheme.selection
        case .general, .about:
            return .secondary
        }
    }

    var selectionTint: Color {
        switch self {
        case .gateway, .routing, .keys, .privacy, .help:
            return WayfinderTheme.selectionTint.opacity(0.84)
        case .general, .about:
            return Color.secondary.opacity(0.12)
        }
    }
}
