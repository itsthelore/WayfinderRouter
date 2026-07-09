import Foundation

@MainActor
public final class AppState: ObservableObject {
    @Published public var prompt = ""
    @Published public private(set) var analysis: PromptAnalysisState = .idle
    @Published public var statsRange: StatsRange = .today
    @Published public private(set) var routingStats: RoutingStats
    @Published public private(set) var isRefreshingStats = false
    @Published public var chatDraft = ""
    @Published public private(set) var chatMessages: [ChatMessage]
    @Published public private(set) var isSendingMessage = false
    @Published public var selectedSettingsSection: SettingsSection = .keys
    @Published public var selectedProvider: ProviderKind = .anthropic

    private let client: any WayfinderClient

    public init(client: any WayfinderClient) {
        self.client = client
        self.routingStats = .mock
        self.chatMessages = .mockConversation
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
        let range = statsRange
        Task {
            do {
                let stats = try await client.loadStats(range: range)
                await MainActor.run {
                    self.routingStats = stats
                    self.isRefreshingStats = false
                }
            } catch {
                await MainActor.run {
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
    case general = "General"
    case gateway = "Gateway"
    case routing = "Routing"
    case keys = "Keys"
    case privacy = "Privacy"
    case help = "Help"
    case about = "About"

    public var id: String { rawValue }

    public var symbolName: String {
        switch self {
        case .general:
            return "slider.horizontal.3"
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
    static var mock: RoutingStats {
        RoutingStats(
            localPercent: 0.68,
            cloudPercent: 0.32,
            savedToday: Decimal(string: "0.01") ?? 0,
            savedLast30Days: Decimal(string: "0.01") ?? 0,
            cloudSpendToday: Decimal(string: "0.00") ?? 0,
            percentVsAlwaysCloud: 0.29,
            averageRoutingTimeMilliseconds: 0.82,
            updatedAt: Date(),
            isRunning: true
        )
    }
}

private extension Array where Element == ChatMessage {
    static var mockConversation: [ChatMessage] {
        [
            ChatMessage(
                role: .user,
                text: "summarize the wf-adr-0044 config seam",
                createdAt: Date().addingTimeInterval(-180)
            ),
            ChatMessage(
                role: .router,
                text: "Routing decisions stay local.",
                decision: RoutingDecision(
                    prompt: "summarize the wf-adr-0044 config seam",
                    route: .local,
                    provider: "local",
                    score: 0.18,
                    mode: "tiered",
                    explanation: "Routing decisions stay local.",
                    features: [
                        RoutingFeature(name: "word_count", value: "6", contribution: 0.04),
                        RoutingFeature(name: "code_block_count", value: "0", contribution: 0),
                        RoutingFeature(name: "structured_sections", value: "none", contribution: 0),
                    ],
                    createdAt: Date().addingTimeInterval(-175)
                ),
                createdAt: Date().addingTimeInterval(-175)
            ),
            ChatMessage(
                role: .user,
                text: "Design a scalable data pipeline in Python with retries and monitoring. Include code.",
                createdAt: Date().addingTimeInterval(-80)
            ),
            ChatMessage(
                role: .router,
                text: "Routed to cloud because the prompt includes code, multiple constraints, and a structured implementation request.",
                decision: RoutingDecision(
                    prompt: "Design a scalable data pipeline in Python with retries and monitoring. Include code.",
                    route: .cloud,
                    provider: "claude-sonnet-4-6",
                    score: 0.82,
                    mode: "tiered",
                    explanation: "Routed to cloud because the prompt includes code, multiple constraints, and a structured implementation request.",
                    features: [
                        RoutingFeature(name: "code_block_count", value: "requested", contribution: 0.35),
                        RoutingFeature(name: "constraint_term_count", value: "3", contribution: 0.24),
                        RoutingFeature(name: "list_item_count", value: "multi-step", contribution: 0.18),
                    ],
                    createdAt: Date().addingTimeInterval(-76)
                ),
                createdAt: Date().addingTimeInterval(-76)
            ),
        ]
    }
}
