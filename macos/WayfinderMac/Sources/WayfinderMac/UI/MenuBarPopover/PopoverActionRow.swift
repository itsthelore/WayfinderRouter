import SwiftUI

public struct PopoverActionRow: View {
    let symbolName: String
    let title: String
    let accessibilityLabel: String
    let accessibilityHint: String
    let shortcut: String?
    let trailingText: String?
    let trailing: String?
    let isEnabled: Bool
    let action: () -> Void

    public init(
        symbolName: String,
        title: String,
        accessibilityLabel: String? = nil,
        accessibilityHint: String,
        shortcut: String? = nil,
        trailingText: String? = nil,
        trailing: String? = nil,
        isEnabled: Bool = true,
        action: @escaping () -> Void
    ) {
        self.symbolName = symbolName
        self.title = title
        self.accessibilityLabel = accessibilityLabel ?? title
        self.accessibilityHint = accessibilityHint
        self.shortcut = shortcut
        self.trailingText = trailingText
        self.trailing = trailing
        self.isEnabled = isEnabled
        self.action = action
    }

    public var body: some View {
        PopoverMenuRow(
            symbolName: symbolName,
            title: title,
            accessibilityLabel: accessibilityLabel,
            accessibilityHint: accessibilityHint,
            isEnabled: isEnabled,
            action: action
        ) {
            if let shortcut {
                Text(shortcut)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
            if let trailingText {
                Text(trailingText)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
            if let trailing {
                Image(systemName: trailing)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
            }
        }
    }
}

struct PopoverMenuRow<Accessory: View>: View {
    let symbolName: String
    let title: String
    let accessibilityLabel: String
    let accessibilityHint: String
    let isEnabled: Bool
    let action: () -> Void
    @ViewBuilder let accessory: () -> Accessory

    @State private var hovering = false

    init(
        symbolName: String,
        title: String,
        accessibilityLabel: String? = nil,
        accessibilityHint: String,
        isEnabled: Bool = true,
        action: @escaping () -> Void,
        @ViewBuilder accessory: @escaping () -> Accessory
    ) {
        self.symbolName = symbolName
        self.title = title
        self.accessibilityLabel = accessibilityLabel ?? title
        self.accessibilityHint = accessibilityHint
        self.isEnabled = isEnabled
        self.action = action
        self.accessory = accessory
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: NativeMenuMetrics.rowSpacing) {
                NativeMenuSymbol(symbolName: symbolName, isEnabled: isEnabled)

                Text(title)
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(isEnabled ? .primary : .secondary)

                Spacer(minLength: 8)
                accessory()
            }
            .frame(height: NativeMenuMetrics.rowHeight)
            .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
            .contentShape(Rectangle())
            .background(isEnabled && hovering ? Color.primary.opacity(0.06) : Color.clear)
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
        .onHover { hovering = $0 }
        .accessibilityLabel(Text(accessibilityLabel))
        .accessibilityHint(Text(accessibilityHint))
    }
}

enum NativeMenuMetrics {
    static let horizontalPadding: CGFloat = 14
    static let sectionHorizontalPadding: CGFloat = 20
    static let rowSpacing: CGFloat = 10
    static let symbolSlotWidth: CGFloat = 16
    static let headerHeight: CGFloat = 36
    static let rowHeight: CGFloat = 36
    static let routingRowHeight: CGFloat = 58
}

struct NativeMenuSymbol: View {
    let symbolName: String
    var isEnabled = true

    var body: some View {
        Image(systemName: symbolName)
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(Color.secondary.opacity(isEnabled ? 1 : 0.55))
            .frame(width: NativeMenuMetrics.symbolSlotWidth)
            .accessibilityHidden(true)
    }
}

struct NativeMenuSeparator: View {
    var body: some View {
        Rectangle()
            .fill(Color(nsColor: .separatorColor).opacity(0.42))
            .frame(height: 1)
            .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
            .accessibilityHidden(true)
    }
}
