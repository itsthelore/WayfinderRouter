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
    @Published public var chatDestination: ChatDestination = .automatic
    @Published public private(set) var chatDestinations: [ChatDestination] = [.automatic]
    @Published public private(set) var chatMessages: [ChatMessage]
    @Published public private(set) var isSendingMessage = false
    @Published public private(set) var setupAssessment: SetupAssessment = .checking

    private let client: any WayfinderClient
    private let setupService = SetupService()
    private var chatTask: Task<Void, Never>?

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
        !chatDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !isSendingMessage
            && chatDestination.isAvailable
    }

    public var canClearChat: Bool {
        !chatMessages.isEmpty && !isSendingMessage
    }

    public var canRetryChat: Bool {
        !isSendingMessage && chatMessages.last.map {
            $0.role == .assistant && ($0.state == .failed || $0.state == .stopped)
        } == true
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
                    self.updateChatDestinations(from: self.gatewayOverview)
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

    public func refreshSetupAssessment() {
        Task {
            let assessment = await setupService.assess()
            await MainActor.run { self.setupAssessment = assessment }
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

        let requestMessages = Self.chatRequestMessages(from: chatMessages) + [
            ChatRequestMessage(role: "user", content: input)
        ]
        let destination = chatDestination
        let responseID = UUID()
        chatDraft = ""
        isSendingMessage = true
        chatMessages.append(ChatMessage(role: .user, text: input))
        chatMessages.append(ChatMessage(
            id: responseID,
            role: .assistant,
            text: "",
            state: .streaming
        ))

        chatTask = Task {
            do {
                for try await event in client.streamChat(
                    messages: requestMessages,
                    destination: destination
                ) {
                    guard let index = self.chatMessages.firstIndex(where: { $0.id == responseID }) else {
                        continue
                    }
                    switch event {
                    case let .decision(decision):
                        self.chatMessages[index].decision = decision
                    case let .text(fragment):
                        self.chatMessages[index].text += fragment
                    case .completed:
                        self.chatMessages[index].state = self.chatMessages[index].text.isEmpty
                            ? .failed
                            : .complete
                        if self.chatMessages[index].text.isEmpty {
                            self.chatMessages[index].text = "No model reply was delivered. Check the configured endpoint in Settings."
                        }
                    }
                }
                if Task.isCancelled {
                    self.finishStoppedChatMessage(id: responseID)
                    return
                }
                if let index = self.chatMessages.firstIndex(where: { $0.id == responseID }),
                   self.chatMessages[index].state == .streaming {
                    self.finishFailedChatMessage(
                        id: responseID,
                        message: WayfinderClientError.invalidChatStream.localizedDescription
                    )
                    return
                }
                self.isSendingMessage = false
                self.chatTask = nil
                self.refreshStats()
            } catch {
                if Task.isCancelled || error is CancellationError {
                    self.finishStoppedChatMessage(id: responseID)
                } else {
                    self.finishFailedChatMessage(
                        id: responseID,
                        message: Self.chatErrorMessage(error, destination: destination)
                    )
                }
            }
        }
    }

    public func stopChatResponse() {
        chatTask?.cancel()
    }

    public func clearChat() {
        guard canClearChat else {
            return
        }
        chatMessages.removeAll()
    }

    public func retryLastChatTurn() {
        guard !isSendingMessage,
              let responseIndex = chatMessages.lastIndex(where: {
                  $0.role == .assistant && ($0.state == .failed || $0.state == .stopped)
              }),
              responseIndex > 0,
              chatMessages[responseIndex - 1].role == .user else {
            return
        }
        let prompt = chatMessages[responseIndex - 1].text
        chatMessages.removeSubrange((responseIndex - 1)...responseIndex)
        chatDraft = prompt
        sendChatDraft()
    }

    static func chatRequestMessages(from messages: [ChatMessage]) -> [ChatRequestMessage] {
        messages.compactMap { message in
            guard message.state == .complete, !message.text.isEmpty else {
                return nil
            }
            return ChatRequestMessage(
                role: message.role == .user ? "user" : "assistant",
                content: message.text
            )
        }
    }

    nonisolated static func chatDestinations(from overview: GatewayOverview) -> [ChatDestination] {
        let configured = overview.endpoints
            .filter { $0.isChatDestinationAvailable && $0.name != "auto" }
            .map(ChatDestination.init(endpoint:))
        return [.automatic] + configured
    }

    nonisolated static func chatErrorMessage(
        _ error: Error,
        destination: ChatDestination
    ) -> String {
        if destination.isChatGPTAccount,
           let clientError = error as? WayfinderClientError,
           case .gatewayStatus(503, _) = clientError {
            return "ChatGPT is not connected or its Codex model is unavailable. Check Accounts in Settings, then retry."
        }
        return error.localizedDescription
    }

    private func updateChatDestinations(from overview: GatewayOverview) {
        var destinations = Self.chatDestinations(from: overview)
        if let refreshed = destinations.first(where: { $0.id == chatDestination.id }) {
            chatDestination = refreshed
        } else if !chatDestination.isAutomatic {
            let unavailable = chatDestination.withAvailability(false)
            destinations.append(unavailable)
            chatDestination = unavailable
        }
        chatDestinations = destinations
    }

    private func finishStoppedChatMessage(id: UUID) {
        if let index = chatMessages.firstIndex(where: { $0.id == id }) {
            chatMessages[index].state = .stopped
            if chatMessages[index].text.isEmpty {
                chatMessages[index].text = "Response stopped."
            }
        }
        isSendingMessage = false
        chatTask = nil
    }

    private func finishFailedChatMessage(id: UUID, message: String) {
        if let index = chatMessages.firstIndex(where: { $0.id == id }) {
            chatMessages[index].state = .failed
            chatMessages[index].text = message
        }
        isSendingMessage = false
        chatTask = nil
    }
}

public enum SettingsSection: String, CaseIterable, Identifiable, Sendable {
    case gateway = "Gateway"
    case routing = "Routing"
    case accounts = "Accounts"
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
        case .accounts:
            return "person.crop.circle"
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
