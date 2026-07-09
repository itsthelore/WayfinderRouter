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
            hairline
            VStack(spacing: 0) {
                StatusRow(
                    symbolName: "server.rack",
                    title: "Gateway",
                    detail: appState.gatewayOverview.gateway.detail,
                    status: appState.gatewayOverview.gateway.title,
                    tint: gatewayTint
                )
                rowDivider
                StatusRow(
                    symbolName: "cloud",
                    title: "Hosted",
                    detail: appState.gatewayOverview.hosted.detail,
                    status: appState.gatewayOverview.hosted.title,
                    tint: hostedTint
                )
                hairline
                RoutingSummarySection(stats: appState.routingStats)
                hairline
                SavedSummarySection(stats: appState.routingStats)
                hairline
                PopoverActionRow(
                    symbolName: "message",
                    title: "Chat",
                    trailing: "chevron.right",
                    action: onOpenChat
                )
                hairline
                footerRows
            }
            .padding(.top, 4)
            Spacer(minLength: 0)
        }
        .frame(width: Self.contentWidth, height: Self.contentHeight, alignment: .top)
        .background {
            Rectangle()
                .fill(.regularMaterial)
            Color(nsColor: .windowBackgroundColor).opacity(0.18)
        }
        .overlay(alignment: .top) {
            Rectangle()
                .fill(Color(nsColor: .separatorColor).opacity(0.35))
                .frame(height: 1)
        }
        .onAppear {
            appState.refreshStats()
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            Text("Wayfinder")
                .font(.system(size: 19, weight: .semibold))

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(appState.gatewayOverview.gateway.title)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.secondary)
                Text(appState.gatewayOverview.updatedAt.relativeUpdateText)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(.tertiary)
            }
        }
        .frame(height: 54)
        .padding(.horizontal, 20)
    }

    private var footerRows: some View {
        VStack(spacing: 0) {
            PopoverActionRow(
                symbolName: "arrow.clockwise",
                title: "Refresh",
                shortcut: "⌘R",
                action: appState.refreshStats
            )
            rowDivider
            PopoverActionRow(
                symbolName: "gearshape",
                title: "Settings...",
                shortcut: "⌘,",
                action: onOpenSettings
            )
            rowDivider
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
            return Color.secondary.opacity(0.58)
        }
    }

    private var hostedTint: Color {
        switch appState.gatewayOverview.hosted {
        case .ready:
            return WayfinderTheme.local
        case .checkKeys:
            return WayfinderTheme.cloud
        case .disabled, .noModels, .unavailable:
            return Color.secondary.opacity(0.58)
        }
    }

    private var hairline: some View {
        Rectangle()
            .fill(Color(nsColor: .separatorColor).opacity(0.55))
            .frame(height: 1)
            .padding(.horizontal, 20)
    }

    private var rowDivider: some View {
        Rectangle()
            .fill(Color(nsColor: .separatorColor).opacity(0.36))
            .frame(height: 1)
            .padding(.leading, 50)
            .padding(.trailing, 20)
    }
}

private struct StatusRow: View {
    let symbolName: String
    let title: String
    let detail: String
    let status: String
    let tint: Color

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbolName)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 20)

            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(.primary)
                Text(detail)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            Spacer()

            Text(status)
                .font(.system(size: 12, weight: .regular))
                .foregroundStyle(.secondary)
        }
        .frame(height: 42)
        .padding(.horizontal, 20)
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
