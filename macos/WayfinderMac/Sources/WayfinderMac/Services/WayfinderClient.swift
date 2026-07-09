import Foundation

public protocol WayfinderClient: Sendable {
    func route(prompt: String) async throws -> RoutingDecision
    func loadStats(range: StatsRange) async throws -> RoutingStats
    func loadOverview() async throws -> GatewayOverview
}

public extension WayfinderClient {
    func analyse(prompt: String) async throws -> RoutingDecision {
        try await route(prompt: prompt)
    }

    func loadOverview() async throws -> GatewayOverview {
        let stats = try await loadStats(range: .today)
        return GatewayOverview(
            gateway: stats.isRunning
                ? .running(detail: "Local router ready")
                : .stopped(detail: "Service stopped"),
            hosted: stats.isRunning
                ? .checkKeys(detail: "Provider keys in Settings")
                : .unavailable(detail: "Start the gateway service"),
            routingStats: stats,
            updatedAt: stats.updatedAt
        )
    }
}

public enum WayfinderClientError: LocalizedError, Equatable {
    case emptyPrompt
    case gatewayResponseMissingDecision
    case gatewayStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .emptyPrompt:
            return "The prompt is empty."
        case .gatewayResponseMissingDecision:
            return "The gateway response did not include a Wayfinder decision."
        case .gatewayStatus(let status):
            return "The gateway returned HTTP \(status)."
        }
    }
}
