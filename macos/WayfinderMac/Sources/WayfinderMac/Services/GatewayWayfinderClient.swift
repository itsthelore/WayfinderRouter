import Foundation

public struct GatewayWayfinderClient: WayfinderClient {
    private let baseURL: URL
    private let session: URLSession

    public init(
        baseURL: URL = URL(string: "http://127.0.0.1:8088")!,
        session: URLSession = .shared
    ) {
        self.baseURL = baseURL
        self.session = session
    }

    public func route(prompt: String) async throws -> RoutingDecision {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw WayfinderClientError.emptyPrompt
        }

        var request = URLRequest(url: baseURL.appending(path: "v1/chat/completions"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("1", forHTTPHeaderField: "X-Wayfinder-Debug")
        request.httpBody = try JSONEncoder().encode(GatewayChatRequest(prompt: trimmed))

        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw WayfinderClientError.gatewayStatus(http.statusCode)
        }

        let decoded = try JSONDecoder().decode(GatewayChatResponse.self, from: data)
        guard let wayfinder = decoded.wayfinder else {
            throw WayfinderClientError.gatewayResponseMissingDecision
        }
        return wayfinder.routingDecision(prompt: trimmed)
    }

    public func loadStats(range: StatsRange) async throws -> RoutingStats {
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

private struct GatewayChatRequest: Encodable {
    let model = "auto"
    let messages: [GatewayMessage]
    let stream = false

    init(prompt: String) {
        self.messages = [GatewayMessage(role: "user", content: prompt)]
    }
}

private struct GatewayMessage: Encodable {
    let role: String
    let content: String
}

private struct GatewayChatResponse: Decodable {
    let wayfinder: GatewayDecision?
}

private struct GatewayDecision: Decodable {
    let model: String?
    let score: Double
    let mode: String?
    let features: [String: Int]?
    let contributions: [GatewayContribution]?
    let tiers: [GatewayTier]?

    func routingDecision(prompt: String) -> RoutingDecision {
        let selectedModel = model ?? naturalModel
        let firstTierModel = tiers?.sorted { $0.minScore < $1.minScore }.first?.model
        let route: RouteTarget = selectedModel == firstTierModel ? .local : .cloud
        let routingFeatures = routedFeatures()

        return RoutingDecision(
            prompt: prompt,
            route: route,
            provider: selectedModel,
            score: score,
            mode: mode ?? "scored",
            explanation: explanation(features: routingFeatures, route: route),
            features: routingFeatures
        )
    }

    private var naturalModel: String {
        let sortedTiers = (tiers ?? []).sorted { $0.minScore < $1.minScore }
        return sortedTiers.reduce(sortedTiers.first?.model ?? "?") { chosen, tier in
            score >= tier.minScore ? tier.model : chosen
        }
    }

    private func routedFeatures() -> [RoutingFeature] {
        if let contributions, !contributions.isEmpty {
            return contributions
                .map {
                    RoutingFeature(
                        name: $0.name,
                        value: "\($0.value)",
                        contribution: $0.contribution
                    )
                }
                .sorted {
                    ($0.contribution ?? 0) == ($1.contribution ?? 0)
                        ? $0.name < $1.name
                        : ($0.contribution ?? 0) > ($1.contribution ?? 0)
                }
        }

        return (features ?? [:])
            .map { RoutingFeature(name: $0.key, value: "\($0.value)", contribution: nil) }
            .sorted { $0.name < $1.name }
    }

    private func explanation(features: [RoutingFeature], route: RouteTarget) -> String {
        let signals = features
            .prefix(3)
            .map(\.label)
            .joined(separator: ", ")

        if signals.isEmpty {
            return "The gateway returned a \(route.displayName.lowercased()) decision."
        }
        return "The gateway returned a \(route.displayName.lowercased()) decision from \(signals)."
    }
}

private struct GatewayContribution: Decodable {
    let name: String
    let value: Int
    let contribution: Double
}

private struct GatewayTier: Decodable {
    let minScore: Double
    let model: String

    private enum CodingKeys: String, CodingKey {
        case minScore = "min_score"
        case model
    }
}
