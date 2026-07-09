import Foundation

public protocol WayfinderClient: Sendable {
    func route(prompt: String) async throws -> RoutingDecision
    func loadStats(range: StatsRange) async throws -> RoutingStats
}

public extension WayfinderClient {
    func analyse(prompt: String) async throws -> RoutingDecision {
        try await route(prompt: prompt)
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
