import AppKit
import SwiftUI

public struct WayfinderPopoverView: View {
    @EnvironmentObject private var appState: AppState

    private let onOpenChat: () -> Void
    private let onOpenSettings: () -> Void
    private let onQuit: () -> Void

    public static let contentWidth: CGFloat = 400
    public static let contentHeight: CGFloat = 460

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
                RoutingSummarySection(stats: appState.routingStats)
                hairline
                ModelStatusSection(stats: appState.routingStats)
                hairline
                SavedSummarySection(stats: appState.routingStats)
                hairline
                actionRows
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 6)
            Spacer(minLength: 0)
        }
        .frame(width: Self.contentWidth, height: Self.contentHeight, alignment: .top)
        .background {
            Rectangle()
                .fill(.regularMaterial)
            Color.white.opacity(0.28)
        }
        .overlay(alignment: .top) {
            Rectangle()
                .fill(Color.white.opacity(0.38))
                .frame(height: 1)
        }
        .preferredColorScheme(.light)
        .onAppear {
            appState.refreshStats()
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            Text("Wayfinder")
                .font(.system(size: 20, weight: .semibold))

            Spacer()

            GatewayStateReadout(isRunning: appState.routingStats.isRunning)
        }
        .frame(height: 56)
        .padding(.horizontal, 16)
    }

    private var actionRows: some View {
        VStack(spacing: 0) {
            PopoverActionRow(
                symbolName: "message",
                title: "Launch Chat (Coming Soon)",
                trailing: "chevron.right",
                isEnabled: false,
                action: {}
            )
            rowDivider
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

    private var hairline: some View {
        Rectangle()
            .fill(Color.primary.opacity(0.12))
            .frame(height: 1)
            .padding(.horizontal, 16)
    }

    private var rowDivider: some View {
        Rectangle()
            .fill(Color.primary.opacity(0.10))
            .frame(height: 1)
    }
}

private struct GatewayStateReadout: View {
    let isRunning: Bool

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(isRunning ? WayfinderTheme.local : Color.secondary.opacity(0.55))
                .frame(width: 7, height: 7)
            Text(isRunning ? "Gateway Running" : "Gateway Stopped")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
        }
        .accessibilityLabel(isRunning ? "Gateway running" : "Gateway stopped")
        .accessibilityAddTraits(.isStaticText)
    }
}
