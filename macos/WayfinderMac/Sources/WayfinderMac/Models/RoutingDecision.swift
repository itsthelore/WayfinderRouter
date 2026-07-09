import Foundation

public struct RoutingDecision: Identifiable, Codable, Equatable, Sendable {
    public let prompt: String
    public let id: UUID
    public let route: RouteTarget
    public let provider: String
    public let score: Double
    public let mode: String
    public let explanation: String
    public let features: [RoutingFeature]
    public let createdAt: Date

    public init(
        id: UUID = UUID(),
        prompt: String,
        route: RouteTarget,
        provider: String,
        score: Double,
        mode: String,
        explanation: String,
        features: [RoutingFeature],
        createdAt: Date = Date()
    ) {
        self.id = id
        self.prompt = prompt
        self.route = route
        self.provider = provider
        self.score = score
        self.mode = mode
        self.explanation = explanation
        self.features = features
        self.createdAt = createdAt
    }

    public var selectedModel: String { provider }
}

public enum RouteTarget: String, Codable, Equatable, Sendable {
    case local
    case cloud

    public var displayName: String {
        switch self {
        case .local:
            return "Local"
        case .cloud:
            return "Cloud"
        }
    }
}
