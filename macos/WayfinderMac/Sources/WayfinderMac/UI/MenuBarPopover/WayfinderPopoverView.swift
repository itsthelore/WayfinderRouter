import AppKit
import SwiftUI

public struct WayfinderPopoverView: View {
    @EnvironmentObject private var appState: AppState

    private let chatAvailability: FeatureAvailability
    private let onOpenChat: (() -> Void)?
    private let onEndpointAnchorFrameChange: (NSRect) -> Void
    private let onEndpointHoverChange: (Bool, NSRect) -> Void
    private let onOpenEndpointStatus: (NSRect) -> Void
    private let onCloseEndpointStatus: () -> Void
    private let onOpenSettings: () -> Void
    private let onQuit: () -> Void

    public static let contentWidth = PopoverPanelSizing.targetWidth
    public static let maximumContentHeight = PopoverPanelSizing.maximumHeight
    public static let contentCornerRadius: CGFloat = 12

    public init(
        chatAvailability: FeatureAvailability,
        onOpenChat: (() -> Void)?,
        onEndpointAnchorFrameChange: @escaping (NSRect) -> Void,
        onEndpointHoverChange: @escaping (Bool, NSRect) -> Void,
        onOpenEndpointStatus: @escaping (NSRect) -> Void,
        onCloseEndpointStatus: @escaping () -> Void,
        onOpenSettings: @escaping () -> Void,
        onQuit: @escaping () -> Void
    ) {
        self.chatAvailability = chatAvailability
        self.onOpenChat = onOpenChat
        self.onEndpointAnchorFrameChange = onEndpointAnchorFrameChange
        self.onEndpointHoverChange = onEndpointHoverChange
        self.onOpenEndpointStatus = onOpenEndpointStatus
        self.onCloseEndpointStatus = onCloseEndpointStatus
        self.onOpenSettings = onOpenSettings
        self.onQuit = onQuit
    }

    public var body: some View {
        WayfinderPopoverContent(
            presentation: PopoverPresentation(overview: appState.gatewayOverview),
            chatAvailability: chatAvailability,
            onOpenChat: onOpenChat,
            onEndpointAnchorFrameChange: onEndpointAnchorFrameChange,
            onEndpointHoverChange: onEndpointHoverChange,
            onOpenEndpointStatus: onOpenEndpointStatus,
            onCloseEndpointStatus: onCloseEndpointStatus,
            onOpenSettings: onOpenSettings,
            onQuit: onQuit
        )
    }
}

struct WayfinderPopoverContent: View {
    let presentation: PopoverPresentation
    let chatAvailability: FeatureAvailability
    let onOpenChat: (() -> Void)?
    let onEndpointAnchorFrameChange: (NSRect) -> Void
    let onEndpointHoverChange: (Bool, NSRect) -> Void
    let onOpenEndpointStatus: (NSRect) -> Void
    let onCloseEndpointStatus: () -> Void
    let onOpenSettings: () -> Void
    let onQuit: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            header

            NativeMenuSeparator()
            RoutingSummarySection(presentation: presentation.routing)

            NativeMenuSeparator()
            EndpointStatusRow(
                presentation: presentation.endpoints,
                onAnchorFrameChange: onEndpointAnchorFrameChange,
                onHoverChange: onEndpointHoverChange,
                onOpen: onOpenEndpointStatus,
                onClose: onCloseEndpointStatus
            )

            NativeMenuSeparator()
            chatRow

            NativeMenuSeparator()
            PopoverActionRow(
                symbolName: "gearshape",
                title: "Settings…",
                accessibilityHint: "Opens Settings. Keyboard shortcut Command-comma.",
                shortcut: "⌘,",
                action: onOpenSettings
            )

            NativeMenuSeparator()
            PopoverActionRow(
                symbolName: "power",
                title: "Quit Wayfinder",
                accessibilityHint: "Quits Wayfinder. Keyboard shortcut Command-Q.",
                shortcut: "⌘Q",
                action: onQuit
            )
        }
        .frame(width: WayfinderPopoverView.contentWidth)
        .fixedSize(horizontal: false, vertical: true)
        .background {
            RoundedRectangle(
                cornerRadius: WayfinderPopoverView.contentCornerRadius,
                style: .continuous
            )
            .fill(.regularMaterial)
        }
        .overlay {
            RoundedRectangle(
                cornerRadius: WayfinderPopoverView.contentCornerRadius,
                style: .continuous
            )
            .stroke(Color(nsColor: .separatorColor).opacity(0.42), lineWidth: 1)
        }
        .clipShape(
            RoundedRectangle(
                cornerRadius: WayfinderPopoverView.contentCornerRadius,
                style: .continuous
            )
        )
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text("Wayfinder")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.primary)

            Spacer(minLength: 8)

            Text(presentation.overallStatus)
                .font(.system(size: 13, weight: .regular))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(height: NativeMenuMetrics.headerHeight)
        .padding(.horizontal, NativeMenuMetrics.sectionHorizontalPadding)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Wayfinder, \(presentation.overallStatus)")
        .accessibilityAddTraits(.isHeader)
    }

    private var chatRow: some View {
        let row = ChatPopoverRowModel(availability: chatAvailability)
        return PopoverActionRow(
            symbolName: "message",
            title: "Chat",
            accessibilityLabel: row.accessibilityLabel,
            accessibilityHint: row.accessibilityHint,
            trailingText: row.trailingText,
            trailing: row.showsChevron ? "chevron.right" : nil,
            isEnabled: row.isEnabled && onOpenChat != nil,
            action: { onOpenChat?() }
        )
    }
}
