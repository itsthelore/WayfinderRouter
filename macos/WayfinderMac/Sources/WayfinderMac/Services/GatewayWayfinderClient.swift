import Foundation

public struct GatewayWayfinderClient: WayfinderClient {
    private let baseURL: URL
    private let session: URLSession
    private let serviceController: GatewayServiceController

    public init(
        baseURL: URL = URL(string: "http://127.0.0.1:8088")!,
        session: URLSession = .shared,
        serviceController: GatewayServiceController = GatewayServiceController()
    ) {
        self.baseURL = baseURL
        self.session = session
        self.serviceController = serviceController
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
        try await loadOverview().routingStats
    }

    public func loadOverview() async throws -> GatewayOverview {
        let serviceStatus = await serviceController.status()
        async let modelsTask = optionalFetch(GatewayModelsResponse.self, path: "router/models")
        async let recentTask = optionalFetch(GatewayRecentResponse.self, path: "router/recent")
        async let savingsTodayTask = optionalFetch(GatewaySavingsResponse.self, path: "v1/savings", queryItems: [
            URLQueryItem(name: "period", value: "today")
        ])
        async let savingsThirtyDaysTask = optionalFetch(GatewaySavingsResponse.self, path: "v1/savings", queryItems: [
            URLQueryItem(name: "period", value: "30d")
        ])

        let models = await modelsTask
        let recent = await recentTask
        let savingsToday = await savingsTodayTask
        let savingsThirtyDays = await savingsThirtyDaysTask
        let updatedAt = Date()
        let gateway = Self.gatewayDisplayState(from: serviceStatus)
        let hosted = Self.hostedDisplayState(
            gateway: gateway,
            health: serviceStatus.health,
            models: models?.models ?? []
        )
        let stats = Self.routingStats(
            gateway: gateway,
            models: models?.models ?? [],
            recent: recent,
            savingsToday: savingsToday,
            savingsThirtyDays: savingsThirtyDays,
            updatedAt: updatedAt
        )

        return GatewayOverview(
            gateway: gateway,
            hosted: hosted,
            routingStats: stats,
            updatedAt: updatedAt
        )
    }

    static func gatewayDisplayState(from status: GatewayServiceStatus) -> GatewayDisplayState {
        guard status.installed else {
            return .notInstalled(detail: "Install the launch agent in Settings")
        }
        guard status.loaded else {
            return .stopped(detail: "Launch agent is not loaded")
        }
        guard let health = status.health else {
            return .unreachable(detail: status.launchConfiguration.healthURLString)
        }
        if health.offline {
            return .offline(detail: "Offline mode keeps delivery local")
        }
        if health.status == "degraded" || !health.missingKeys.isEmpty {
            let detail = health.missingKeys.isEmpty
                ? "Gateway is reporting degraded health"
                : "Missing \(health.missingKeys.joined(separator: ", "))"
            return .degraded(detail: detail)
        }
        return .running(detail: health.models.isEmpty ? "No models configured" : "\(health.models.count) configured model\(health.models.count == 1 ? "" : "s")")
    }

    static func hostedDisplayState(
        gateway: GatewayDisplayState,
        health: GatewayHealth?,
        models: [GatewayModelInfo]
    ) -> HostedDisplayState {
        switch gateway {
        case .offline:
            return .disabled(detail: "Gateway offline mode is on")
        case .stopped, .unreachable, .notInstalled:
            return .unavailable(detail: "Gateway is not reachable")
        case .running, .degraded:
            break
        }

        if health?.models.isEmpty == true || models.isEmpty {
            return .noModels(detail: "Add models in Settings")
        }

        let missingKeyNames = models
            .filter { !$0.keyOK }
            .map { $0.apiKeyEnv ?? $0.name }
        if !(health?.missingKeys ?? []).isEmpty || !missingKeyNames.isEmpty {
            let names = missingKeyNames.isEmpty ? (health?.missingKeys ?? []) : missingKeyNames
            return .checkKeys(detail: names.joined(separator: ", "))
        }

        return .ready(detail: "\(models.count) configured model\(models.count == 1 ? "" : "s")")
    }

    static func routingStats(
        gateway: GatewayDisplayState,
        models: [GatewayModelInfo],
        recent: GatewayRecentResponse?,
        savingsToday: GatewaySavingsResponse?,
        savingsThirtyDays: GatewaySavingsResponse?,
        updatedAt: Date
    ) -> RoutingStats {
        let cheapestModel = models.first?.name
        let totalTurns = recent?.total ?? 0
        let byModel = recent?.byModel ?? [:]
        let localCount = cheapestModel.map { byModel[$0, default: 0] } ?? 0
        let cloudCount = max(0, totalTurns - localCount)
        let localPercent = totalTurns > 0 ? Double(localCount) / Double(totalTurns) : 0
        let cloudPercent = totalTurns > 0 ? Double(cloudCount) / Double(totalTurns) : 0
        let hasSavings = savingsToday?.hasDisplayableSavings == true || savingsThirtyDays?.hasDisplayableSavings == true

        return RoutingStats(
            localPercent: localPercent,
            cloudPercent: cloudPercent,
            localRouteCount: totalTurns > 0 ? localCount : nil,
            cloudRouteCount: totalTurns > 0 ? cloudCount : nil,
            totalTurns: totalTurns,
            savedToday: savingsToday?.savedDecimal ?? 0,
            savedLast30Days: savingsThirtyDays?.savedDecimal ?? 0,
            cloudSpendToday: savingsToday?.baselineDecimal ?? 0,
            percentVsAlwaysCloud: savingsToday?.savedPercentFraction ?? 0,
            isPriced: savingsToday?.priced == true || savingsThirtyDays?.priced == true,
            hasSavings: hasSavings,
            savedTodayDisplay: savingsToday?.displayLine(period: "Today") ?? "Today: Not yet available",
            savedLast30DaysDisplay: savingsThirtyDays?.displayLine(period: "Last 30 days") ?? "Last 30 days: Not yet available",
            averageRoutingTimeMilliseconds: 0,
            updatedAt: updatedAt,
            isRunning: gateway.isRunning
        )
    }

    private func optionalFetch<T: Decodable>(
        _ type: T.Type,
        path: String,
        queryItems: [URLQueryItem] = []
    ) async -> T? {
        do {
            return try await fetch(type, path: path, queryItems: queryItems)
        } catch {
            return nil
        }
    }

    private func fetch<T: Decodable>(
        _ type: T.Type,
        path: String,
        queryItems: [URLQueryItem] = []
    ) async throws -> T {
        var components = URLComponents(url: baseURL.appending(path: path), resolvingAgainstBaseURL: false)!
        components.queryItems = queryItems.isEmpty ? nil : queryItems
        let url = components.url!
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw WayfinderClientError.gatewayStatus(http.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: data)
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

struct GatewayModelsResponse: Decodable, Equatable, Sendable {
    let models: [GatewayModelInfo]
    let dryRun: Bool

    private enum CodingKeys: String, CodingKey {
        case models
        case dryRun = "dry_run"
    }
}

struct GatewayModelInfo: Decodable, Equatable, Sendable {
    let name: String
    let endpoint: String
    let model: String
    let apiKeyEnv: String?
    let keyOK: Bool

    private enum CodingKeys: String, CodingKey {
        case name
        case endpoint
        case model
        case apiKeyEnv = "api_key_env"
        case keyOK = "key_ok"
    }
}

struct GatewayRecentResponse: Decodable, Equatable, Sendable {
    let total: Int
    let byModel: [String: Int]
    let recent: [GatewayRecentRoute]

    private enum CodingKeys: String, CodingKey {
        case total
        case byModel = "by_model"
        case recent
    }
}

struct GatewayRecentRoute: Decodable, Equatable, Sendable {
    let requestID: String
    let model: String
    let score: Double
    let mode: String
    let timestamp: Double

    private enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case model
        case score
        case mode
        case timestamp = "ts"
    }
}

struct GatewaySavingsResponse: Decodable, Equatable, Sendable {
    let saved: Double
    let savedPercent: Double
    let priced: Bool
    let requests: Int
    let baseline: Double?

    private enum CodingKeys: String, CodingKey {
        case saved
        case savedPercent = "saved_pct"
        case priced
        case requests
        case baseline
    }

    var savedDecimal: Decimal {
        Decimal(saved)
    }

    var baselineDecimal: Decimal {
        Decimal(baseline ?? 0)
    }

    var savedPercentFraction: Double {
        savedPercent / 100
    }

    var hasDisplayableSavings: Bool {
        priced && requests > 0 && saved > 0
    }

    func displayLine(period: String) -> String {
        guard hasDisplayableSavings else {
            return "\(period): Not yet available"
        }
        let savedText = saved < 0.01 ? "<$0.01" : savedDecimal.currencyText
        return "\(period): \(savedText) · \(savedPercentFraction.percentText) vs always-cloud"
    }
}
