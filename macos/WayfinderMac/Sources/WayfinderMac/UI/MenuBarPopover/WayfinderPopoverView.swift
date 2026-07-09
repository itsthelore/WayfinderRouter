import AppKit
import SwiftUI

public struct WayfinderPopoverView: View {
    @EnvironmentObject private var appState: AppState

    private let onOpenChat: () -> Void
    private let onOpenSettings: () -> Void
    private let onQuit: () -> Void

    public static let contentWidth: CGFloat = 400
    public static let contentHeight: CGFloat = 550

    public init(
        onOpenChat: @escaping () -> Void,
        onOpenSettings: @escaping () -> Void,
        onQuit: @escaping () -> Void
    ) {
        self.onOpenChat = onOpenChat
        self.onOpenSettings = onOpenSettings
        self.onQuit = onQuit
    }

    public var body: some View {
        VStack(spacing: 0) {
            header
            NativeMenuSeparator()

            StatusRow(
                symbolName: "server.rack",
                title: "Gateway",
                detail: appState.gatewayOverview.gateway.detail,
                status: appState.gatewayOverview.gateway.title,
                tint: gatewayTint,
                filled: appState.gatewayOverview.gateway.isRunning
            )
            NativeMenuSeparator(leadingInset: 80)

            StatusRow(
                symbolName: "cloud",
                title: "Hosted",
                detail: appState.gatewayOverview.hosted.detail,
                status: appState.gatewayOverview.hosted.title,
                tint: hostedTint,
                filled: hostedIsReady
            )

            NativeMenuSeparator()
            RoutingSummarySection(stats: appState.routingStats)
            NativeMenuSeparator(leadingInset: 80)
            SavedSummarySection(stats: appState.routingStats)

            NativeMenuSeparator()
            PopoverActionRow(
                symbolName: "message",
                title: "Chat",
                trailing: "chevron.right",
                action: onOpenChat
            )

            Spacer(minLength: 0)
            NativeMenuSeparator()
            footerRows
        }
        .frame(width: Self.contentWidth, height: Self.contentHeight, alignment: .top)
        .background {
            Rectangle()
                .fill(.regularMaterial)
            Color(nsColor: .windowBackgroundColor).opacity(0.08)
        }
        .overlay {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(Color(nsColor: .separatorColor).opacity(0.35), lineWidth: 1)
        }
        .onAppear {
            appState.refreshStats()
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 14) {
            VStack(alignment: .leading, spacing: 7) {
                Text("Wayfinder")
                    .font(.system(size: 23, weight: .bold))
                    .foregroundStyle(.primary)
                Text(appState.gatewayOverview.updatedAt.relativeUpdateText)
                    .font(.system(size: 14, weight: .regular))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Text(appState.gatewayOverview.gateway.title)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Color.primary.opacity(0.07), in: Capsule())
        }
        .frame(height: 72)
        .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
    }

    private var footerRows: some View {
        VStack(spacing: 0) {
            PopoverActionRow(
                symbolName: "arrow.clockwise",
                title: "Refresh",
                shortcut: "⌘R",
                action: appState.refreshStats
            )
            NativeMenuSeparator(leadingInset: 80)
            PopoverActionRow(
                symbolName: "gearshape",
                title: "Settings...",
                shortcut: "⌘,",
                action: onOpenSettings
            )
            NativeMenuSeparator(leadingInset: 80)
            PopoverActionRow(
                symbolName: "power",
                title: "Quit Wayfinder",
                shortcut: "⌘Q",
                action: onQuit
            )
        }
    }

    private var gatewayTint: Color {
        switch appState.gatewayOverview.gateway {
        case .running, .offline:
            return WayfinderTheme.local
        case .degraded:
            return WayfinderTheme.cloud
        case .stopped, .unreachable, .notInstalled:
            return Color.secondary.opacity(0.65)
        }
    }

    private var hostedTint: Color {
        switch appState.gatewayOverview.hosted {
        case .ready:
            return WayfinderTheme.local
        case .checkKeys:
            return WayfinderTheme.cloud
        case .disabled, .noModels, .unavailable:
            return Color.secondary.opacity(0.65)
        }
    }

    private var hostedIsReady: Bool {
        switch appState.gatewayOverview.hosted {
        case .ready:
            return true
        case .checkKeys, .disabled, .noModels, .unavailable:
            return false
        }
    }
}

private struct StatusRow: View {
    let symbolName: String
    let title: String
    let detail: String
    let status: String
    let tint: Color
    let filled: Bool

    var body: some View {
        HStack(spacing: NativeMenuMetrics.rowSpacing) {
            NativeMenuIconWell(symbolName: symbolName, tint: tint, filled: filled)

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.primary)
                Text(detail)
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            Spacer()

            Text(status)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(height: NativeMenuMetrics.statusRowHeight)
        .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
        .contentShape(Rectangle())
        .accessibilityLabel("\(title), \(status), \(detail)")
        .accessibilityAddTraits(.isStaticText)
    }
}

private extension Date {
    var relativeUpdateText: String {
        let delta = max(0, Int(Date().timeIntervalSince(self)))
        if delta < 10 { return "Updated just now" }
        if delta < 60 { return "Updated \(delta)s ago" }
        let minutes = delta / 60
        if minutes < 60 { return "Updated \(minutes)m ago" }
        return "Updated \(minutes / 60)h ago"
    }
}
