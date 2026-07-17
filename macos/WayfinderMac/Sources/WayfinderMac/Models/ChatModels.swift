import Foundation

public enum ChatMessageRole: Codable, Equatable, Sendable {
    case user
    case assistant
}

public enum ChatMessageState: Codable, Equatable, Sendable {
    case streaming
    case complete
    case stopped
    case failed
}

public struct ChatMessage: Identifiable, Codable, Equatable, Sendable {
    public let id: UUID
    public let role: ChatMessageRole
    public var text: String
    public var decision: RoutingDecision?
    public var state: ChatMessageState
    public let createdAt: Date

    public init(
        id: UUID = UUID(),
        role: ChatMessageRole,
        text: String,
        decision: RoutingDecision? = nil,
        state: ChatMessageState = .complete,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.decision = decision
        self.state = state
        self.createdAt = createdAt
    }
}

public struct ChatRequestMessage: Codable, Equatable, Sendable {
    public let role: String
    public let content: String

    public init(role: String, content: String) {
        self.role = role
        self.content = content
    }
}

public enum ChatStreamEvent: Equatable, Sendable {
    case decision(RoutingDecision)
    case text(String)
    case completed
}
