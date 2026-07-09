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
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(.secondary)
            }
            if let trailing {
                Image(systemName: trailing)
                    .font(.system(size: 16, weight: .regular))
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
            HStack(spacing: NativeMenuMetrics.rowSpacing) {
                NativeMenuIconWell(
                    symbolName: symbolName,
                    tint: isEnabled ? Color.secondary : Color.secondary.opacity(0.45)
                )
                Text(title)
                    .font(.system(size: 16, weight: .regular))
                    .foregroundStyle(isEnabled ? .primary : .secondary)
                Spacer()
                accessory()
            }
            .frame(height: NativeMenuMetrics.actionRowHeight)
            .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
            .contentShape(Rectangle())
            .background(isEnabled && hovering ? Color.primary.opacity(0.06) : Color.clear)
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
        .onHover { hovering = $0 }
    }
}

enum NativeMenuMetrics {
    static let horizontalPadding: CGFloat = 26
    static let rowSpacing: CGFloat = 16
    static let iconWellSize: CGFloat = 36
    static let statusRowHeight: CGFloat = 58
    static let metricRowHeight: CGFloat = 75
    static let actionRowHeight: CGFloat = 44
}

struct NativeMenuIconWell: View {
    let symbolName: String
    let tint: Color
    var filled: Bool = false

    var body: some View {
        ZStack {
            Circle()
                .fill(filled ? tint : Color.primary.opacity(0.08))
            Image(systemName: symbolName)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(filled ? Color.white : tint)
        }
        .frame(width: NativeMenuMetrics.iconWellSize, height: NativeMenuMetrics.iconWellSize)
        .accessibilityHidden(true)
    }
}

struct NativeMenuSectionHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
            .padding(.top, 14)
            .padding(.bottom, 5)
    }
}

struct NativeMenuSeparator: View {
    var leadingInset: CGFloat = NativeMenuMetrics.horizontalPadding

    var body: some View {
        Rectangle()
            .fill(Color(nsColor: .separatorColor).opacity(0.42))
            .frame(height: 1)
            .padding(.leading, leadingInset)
            .padding(.trailing, NativeMenuMetrics.horizontalPadding)
    }
}
