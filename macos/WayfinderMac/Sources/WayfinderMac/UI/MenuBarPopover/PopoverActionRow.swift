import SwiftUI

public struct PopoverActionRow: View {
    let symbolName: String
    let title: String
    let shortcut: String?
    let trailing: String?
    let isEnabled: Bool
    let action: () -> Void

    @State private var hovering = false

    public init(
        symbolName: String,
        title: String,
        shortcut: String? = nil,
        trailing: String? = nil,
        isEnabled: Bool = true,
        action: @escaping () -> Void
    ) {
        self.symbolName = symbolName
        self.title = title
        self.shortcut = shortcut
        self.trailing = trailing
        self.isEnabled = isEnabled
        self.action = action
    }

    public var body: some View {
        PopoverMenuRow(
            symbolName: symbolName,
            title: title,
            isEnabled: isEnabled,
            action: action
        ) {
            if let shortcut {
                Text(shortcut)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
            }
            if let trailing {
                Image(systemName: trailing)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

struct PopoverMenuRow<Accessory: View>: View {
    let symbolName: String
    let title: String
    let isEnabled: Bool
    let action: () -> Void
    @ViewBuilder let accessory: () -> Accessory

    @State private var hovering = false

    init(
        symbolName: String,
        title: String,
        isEnabled: Bool = true,
        action: @escaping () -> Void,
        @ViewBuilder accessory: @escaping () -> Accessory
    ) {
        self.symbolName = symbolName
        self.title = title
        self.isEnabled = isEnabled
        self.action = action
        self.accessory = accessory
    }

    var body: some View {
        Button(action: {
            guard isEnabled else { return }
            action()
        }) {
            HStack(spacing: 9) {
                Image(systemName: symbolName)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(isEnabled ? .secondary : .tertiary)
                    .frame(width: 14)
                Text(title)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(isEnabled ? .primary : .secondary)
                Spacer()
                accessory()
            }
            .frame(height: 28)
            .contentShape(Rectangle())
            .background(isEnabled && hovering ? Color.primary.opacity(0.06) : Color.clear)
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
        .onHover { hovering = $0 }
    }
}
