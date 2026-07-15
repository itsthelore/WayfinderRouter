import Foundation

@MainActor
public final class AppState: ObservableObject {
    @Published public var prompt = ""
    @Published public private(set) var analysis: PromptAnalysisState = .idle
    @Published public var statsRange: StatsRange = .today
    @Published public private(set) var routingStats: RoutingStats
    @Published public private(set) var gatewayOverview: GatewayOverview
    @Published public private(set) var isRefreshingStats = false
    @Published public var chatDraft = ""
    @Published public private(set) var chatMessages: [ChatMessage]
    @Published public private(set) var isSendingMessage = false

    private let client: any WayfinderClient

    public init(client: any WayfinderClient) {
        self.client = client
        self.routingStats = .empty
        self.gatewayOverview = .checking
        self.chatMessages = []
    }

    public var canAnalyse: Bool {
        !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !analysis.isAnalysing
    }

    public var canSendMessage: Bool {
        !chatDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isSendingMessage
    }

    public func analyse() {
        let input = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty, !analysis.isAnalysing else {
            return
        }

        analysis = .analysing
        Task {
            do {
                let decision = try await client.route(prompt: input)
                await MainActor.run {
                    self.analysis = .result(decision)
                }
            } catch {
                await MainActor.run {
                    self.analysis = .failed(error.localizedDescription)
                }
            }
        }
    }

    public func clear() {
        prompt = ""
        analysis = .idle
    }

    public func refreshStats() {
        guard !isRefreshingStats else {
            return
        }

        isRefreshingStats = true
        Task {
            do {
                let overview = try await client.loadOverview()
                await MainActor.run {
                    self.gatewayOverview = overview.preservingUnavailableEndpoints(
                        from: self.gatewayOverview.endpoints
                    )
                    self.routingStats = overview.routingStats
                    self.isRefreshingStats = false
                }
            } catch {
                await MainActor.run {
                    self.gatewayOverview = GatewayOverview
                        .unreachable(error.localizedDescription)
                        .preservingUnavailableEndpoints(from: self.gatewayOverview.endpoints)
                    self.routingStats = .empty
                    self.isRefreshingStats = false
                }
            }
        }
    }

    public func selectStatsRange(_ range: StatsRange) {
        statsRange = range
        refreshStats()
    }

    public func sendChatDraft() {
        let input = chatDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty, !isSendingMessage else {
            return
        }

        chatDraft = ""
        isSendingMessage = true
        chatMessages.append(ChatMessage(role: .user, text: input))

        Task {
            do {
                let decision = try await client.route(prompt: input)
                let response = ChatMessage(
                    role: .router,
                    text: decision.explanation,
                    decision: decision
                )
                await MainActor.run {
                    self.chatMessages.append(response)
                    self.isSendingMessage = false
                    self.refreshStats()
                }
            } catch {
                let fallback = ChatMessage(
                    role: .router,
                    text: error.localizedDescription,
                    decision: nil
                )
                await MainActor.run {
                    self.chatMessages.append(fallback)
                    self.isSendingMessage = false
                }
            }
        }
    }
}

public enum SettingsSection: String, CaseIterable, Identifiable, Sendable {
    case gateway = "Gateway"
    case routing = "Routing"
    case keys = "Keys"
    case privacy = "Privacy"
    case help = "Help"
    case about = "About"

    public var id: String { rawValue }

    public var symbolName: String {
        switch self {
        case .gateway:
            return "server.rack"
        case .routing:
            return "point.topleft.down.curvedto.point.bottomright.up"
        case .keys:
            return "key"
        case .privacy:
            return "shield"
        case .help:
            return "questionmark.circle"
        case .about:
            return "info.circle"
        }
    }
}

public enum ProviderKind: String, CaseIterable, Identifiable, Sendable {
    case anthropic = "Anthropic"
    case openAI = "OpenAI"
    case googleGemini = "Google Gemini"
    case ollama = "Ollama"
    case lmStudio = "LM Studio"
    case custom = "Custom"

    public var id: String { rawValue }
}

private extension RoutingStats {
    static var empty: RoutingStats {
        RoutingStats(
            localPercent: 0,
            cloudPercent: 0,
            totalTurns: 0,
            savedToday: 0,
            savedLast30Days: 0,
            cloudSpendToday: 0,
            percentVsAlwaysCloud: 0,
            isPriced: false,
            hasSavings: false,
            savedTodayDisplay: "Today: Not yet available",
            savedLast30DaysDisplay: "Last 30 days: Not yet available",
            averageRoutingTimeMilliseconds: 0,
            updatedAt: Date(),
            isRunning: false
        )
    }
}

private extension GatewayOverview {
    static var checking: GatewayOverview {
        GatewayOverview(
            gateway: .checking(detail: "Checking gateway status"),
            hosted: .checking(detail: "Checking configured models"),
            routingStats: .empty,
            updatedAt: Date()
        )
    }

    static func unreachable(_ detail: String) -> GatewayOverview {
        GatewayOverview(
            gateway: .unreachable(detail: detail),
            hosted: .unavailable(detail: "Gateway is not reachable"),
            routingStats: .empty,
            updatedAt: Date()
        )
    }

    func preservingUnavailableEndpoints(
        from previousEndpoints: [EndpointDisplayStatus]
    ) -> GatewayOverview {
        guard endpoints.isEmpty, !gateway.isRunning, !previousEndpoints.isEmpty else {
            return self
        }
        return GatewayOverview(
            gateway: gateway,
            hosted: hosted,
            endpoints: previousEndpoints.map {
                EndpointDisplayStatus(
                    name: $0.name,
                    providerName: $0.providerName,
                    modelName: $0.modelName,
                    state: .unavailable
                )
            },
            routingStats: routingStats,
            updatedAt: updatedAt
        )
    }
}
