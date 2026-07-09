import Foundation

public enum ChatMessageRole: Codable, Equatable, Sendable {
    case user
    case router
}

public struct ChatMessage: Identifiable, Codable, Equatable, Sendable {
    public let id: UUID
    public let role: ChatMessageRole
    public let text: String
    public let decision: RoutingDecision?
    public let createdAt: Date

    public init(
        id: UUID = UUID(),
        role: ChatMessageRole,
        text: String,
        decision: RoutingDecision? = nil,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.decision = decision
        self.createdAt = createdAt
    }
}
