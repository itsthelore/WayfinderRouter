import AppKit
import SwiftUI

public struct GatewaySettingsView: View {
    @State private var isRestarting = false
    @State private var isRefreshing = false
    @State private var message: GatewayActionMessage?
    @State private var status = GatewayServiceStatus(
        installed: false,
        loaded: false,
        launchConfiguration: GatewayLaunchConfiguration(
            host: GatewayServiceController.defaultHost,
            port: GatewayServiceController.defaultPort,
            configPath: GatewayServiceController.defaultConfigPath()
        ),
        health: nil
    )

    private let service = GatewayServiceController()

    public init() {}

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Gateway")
                    .font(.title3.weight(.semibold))
                Text("Apps connect to this local router. Wayfinder routes each request to a configured endpoint.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            Form {
                Section("Status and connections") {
                gatewayStatusSection
                }

                Section("Use with apps") {
                GatewayExplainerSection()
                }
            }
            .formStyle(.grouped)
        }
        .padding(.horizontal, 28)
        .padding(.top, 24)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task {
            await loadStatus(showSpinner: false)
        }
    }

    private var gatewayStatusSection: some View {
        VStack(spacing: 0) {
            GatewayValueRow(
                title: "Service",
                value: status.statusSummary,
                detail: status.health?.detailSummary ?? GatewayServiceController.launchdLabel,
                symbolName: statusSymbolName,
                tint: statusTint
            )

            GatewayValueRow(
                title: "Local Router",
                value: status.launchConfiguration.localRouterURL,
                detail: localRouterDetail,
                symbolName: "point.3.connected.trianglepath.dotted",
                canCopy: true,
                copyFeedbackTitle: "Local Router",
                onCopy: showCopiedMessage
            )

            GatewayValueRow(
                title: "OpenAI-compatible",
                value: status.launchConfiguration.openAIBaseURL,
                detail: "Use as OPENAI_BASE_URL for OpenAI-shaped clients.",
                symbolName: "link",
                canCopy: true,
                copyFeedbackTitle: "OpenAI-compatible URL",
                onCopy: showCopiedMessage
            )

            GatewayValueRow(
                title: "Anthropic-compatible",
                value: status.launchConfiguration.anthropicRootURL,
                detail: "Use as the base URL for Anthropic-shaped clients.",
                symbolName: "link.badge.plus",
                canCopy: true,
                copyFeedbackTitle: "Anthropic-compatible URL",
                onCopy: showCopiedMessage
            )

            GatewayRouteNamesRow(
                names: routeNames,
                detail: routeNamesDetail,
                onCopy: showCopiedRouteName
            )

            GatewayDiagnosticsDivider()

            GatewayValueRow(
                title: "Health Check",
                value: status.launchConfiguration.healthURLString,
                symbolName: "stethoscope",
                canCopy: true,
                copyFeedbackTitle: "Health Check",
                onCopy: showCopiedMessage
            )

            GatewayValueRow(
                title: "Config",
                value: status.launchConfiguration.configPath,
                symbolName: "doc.text",
                canCopy: true,
                copyFeedbackTitle: "Config",
                onCopy: showCopiedMessage
            )

            Divider()

            HStack(spacing: 8) {
                Button {
                    restartGateway()
                } label: {
                    HStack(spacing: 6) {
                        ProgressView()
                            .controlSize(.small)
                            .opacity(isRestarting ? 1 : 0)
                            .frame(width: 14, height: 14)
                        Text("Restart Gateway")
                    }
                    .frame(minWidth: 132)
                }
                .disabled(isRestarting || isRefreshing)

                Button {
                    refreshStatus()
                } label: {
                    HStack(spacing: 6) {
                        ProgressView()
                            .controlSize(.small)
                            .opacity(isRefreshing ? 1 : 0)
                            .frame(width: 14, height: 14)
                        Text("Refresh Status")
                    }
                    .frame(minWidth: 124)
                }
                .disabled(isRefreshing || isRestarting)

                Button {
                    revealConfig()
                } label: {
                    Text("Show Config")
                }

                Spacer()
            }
            .padding(.horizontal, 12)
            .frame(height: 46)

            Divider()
            InlineGatewayMessage(message: message)
                .padding(.horizontal, 12)
                .frame(height: 38)
        }
    }

    private var configPath: String {
        status.launchConfiguration.configPath
    }

    private var localRouterDetail: String {
        if let bindDescription = status.launchConfiguration.bindDescription {
            return "One shared gateway address for this Mac. \(bindDescription)"
        }
        return "One shared gateway address for this Mac."
    }

    private var routeNames: [String] {
        status.health?.availableRouteNames ?? GatewayHealth.builtInRouteNames
    }

    private var routeNamesDetail: String {
        if status.health == nil {
            return "Built-in route names shown; configured names appear when gateway health is available."
        }
        if status.health?.models.isEmpty == true {
            return "Built-in route names shown; no configured endpoints reported by the gateway."
        }
        return "Use these in the model field to choose routing for an app or request."
    }

    private var statusSymbolName: String {
        if status.health?.offline == true { return "wifi.slash" }
        if status.health?.status == "degraded" { return "exclamationmark.triangle" }
        if status.health?.status == "ok" { return "checkmark.circle" }
        return status.loaded ? "server.rack" : "powerplug"
    }

    private var statusTint: Color {
        if status.health?.offline == true { return .secondary }
        if status.health?.status == "degraded" { return .orange }
        if status.health?.status == "ok" { return WayfinderTheme.local }
        return .secondary
    }

    private func restartGateway() {
        isRestarting = true
        message = nil
        Task {
            do {
                try await service.restart()
                message = GatewayActionMessage(
                    text: "Gateway restart requested.",
                    tint: WayfinderTheme.local,
                    symbolName: "checkmark.circle"
                )
            } catch {
                message = GatewayActionMessage(
                    text: error.localizedDescription,
                    tint: .red,
                    symbolName: "exclamationmark.triangle"
                )
            }
            await loadStatus(showSpinner: false)
            isRestarting = false
        }
    }

    private func refreshStatus() {
        Task {
            await loadStatus(showSpinner: true)
        }
    }

    private func loadStatus(showSpinner: Bool) async {
        if showSpinner {
            isRefreshing = true
            message = nil
        }
        let latest = await service.status()
        status = latest
        if showSpinner {
            message = GatewayActionMessage(
                text: "Gateway status refreshed.",
                tint: WayfinderTheme.local,
                symbolName: "arrow.clockwise.circle"
            )
            isRefreshing = false
        }
    }

    private func revealConfig() {
        let url = URL(fileURLWithPath: configPath)
        NSWorkspace.shared.activateFileViewerSelecting([url])
        message = GatewayActionMessage(
            text: "Showing gateway config.",
            tint: WayfinderTheme.local,
            symbolName: "doc.text.magnifyingglass"
        )
    }

    private func showCopiedMessage(_ title: String) {
        message = GatewayActionMessage(
            text: "Copied \(title)",
            tint: WayfinderTheme.local,
            symbolName: "doc.on.doc"
        )
    }

    private func showCopiedRouteName(_ name: String) {
        message = GatewayActionMessage(
            text: "Copied route \(name)",
            tint: WayfinderTheme.local,
            symbolName: "doc.on.doc"
        )
    }
}

private struct GatewayActionMessage {
    let text: String
    let tint: Color
    let symbolName: String
}

private struct InlineGatewayMessage: View {
    let message: GatewayActionMessage?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: message?.symbolName ?? "checkmark.circle")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(message?.tint ?? .secondary)
                .frame(width: 18)
                .opacity(message == nil ? 0 : 1)
            Text(message?.text ?? " ")
                .font(.caption)
                .foregroundStyle(message?.tint ?? .secondary)
            Spacer()
        }
    }
}

private struct GatewayDiagnosticsDivider: View {
    var body: some View {
        HStack(spacing: 8) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
            Text("Diagnostics")
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
        .padding(.horizontal, 12)
        .frame(height: 28)
    }
}

private struct GatewayRouteNamesRow: View {
    let names: [String]
    let detail: String
    let onCopy: (String) -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "list.bullet.rectangle")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 20)

            Text("Available Routes")
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(width: 126, alignment: .leading)

            VStack(alignment: .leading, spacing: 6) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(names, id: \.self) { name in
                            Button {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(name, forType: .string)
                                onCopy(name)
                            } label: {
                                Text(name)
                                    .font(.caption.monospaced())
                                    .lineLimit(1)
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 4)
                            }
                            .buttonStyle(.borderless)
                            .background(WayfinderTheme.panel.opacity(0.76), in: Capsule())
                            .overlay {
                                Capsule()
                                    .stroke(WayfinderTheme.hairline, lineWidth: 1)
                            }
                            .help("Copy route \(name)")
                            .accessibilityLabel("Copy route \(name)")
                        }
                    }
                    .padding(.vertical, 1)
                }
                .frame(height: 28)

                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            Spacer(minLength: 12)
        }
        .frame(minHeight: 62)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
    }
}

private struct GatewayValueRow: View {
    let title: String
    let value: String
    var detail: String?
    let symbolName: String
    var tint: Color = .secondary
    var canCopy = false
    var copyFeedbackTitle: String?
    var onCopy: ((String) -> Void)?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: symbolName)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 20)

            Text(title)
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(width: 126, alignment: .leading)

            VStack(alignment: .leading, spacing: 2) {
                Text(value)
                    .font(.callout)
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                    .truncationMode(.middle)

                if let detail, !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }

            Spacer(minLength: 12)

            if canCopy {
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(value, forType: .string)
                    onCopy?(copyFeedbackTitle ?? title)
                } label: {
                    Image(systemName: "doc.on.doc")
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: 20, height: 20)
                }
                .buttonStyle(.borderless)
                .help("Copy \(title)")
                .accessibilityLabel("Copy \(title)")
            }
        }
        .frame(minHeight: 44)
        .padding(.horizontal, 12)
        .padding(.vertical, 5)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
    }
}

private struct GatewayExplainerSection: View {
    var body: some View {
        VStack(spacing: 0) {
                GatewayExplainerRow(
                    title: "Connect",
                    value: "Point each app at the URL matching its API shape.",
                    symbolName: "link"
                )
                GatewayExplainerRow(
                    title: "Choose",
                    value: "Use model=\"auto\" to let Wayfinder choose for each request.",
                    symbolName: "arrow.triangle.branch"
                )
                GatewayExplainerRow(
                    title: "Pin",
                    value: "Use prefer-local, prefer-hosted, or a configured route name when an app needs a stable path.",
                    symbolName: "mappin.and.ellipse"
                )
                GatewayExplainerRow(
                    title: "Share",
                    value: "One gateway can serve many apps without running separate services.",
                    symbolName: "point.3.connected.trianglepath.dotted"
                )
        }
    }
}

private struct GatewayExplainerRow: View {
    let title: String
    let value: String
    let symbolName: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: symbolName)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 20)

            Text(title)
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(width: 126, alignment: .leading)

            Text(value)
                .font(.callout)
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 12)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .frame(minHeight: 44)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
    }
}
