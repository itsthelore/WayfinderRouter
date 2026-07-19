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
    public var recoverySettingsSection: SettingsSection?
    public let createdAt: Date

    public init(
        id: UUID = UUID(),
        role: ChatMessageRole,
        text: String,
        decision: RoutingDecision? = nil,
        state: ChatMessageState = .complete,
        recoverySettingsSection: SettingsSection? = nil,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.decision = decision
        self.state = state
        self.recoverySettingsSection = recoverySettingsSection
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

/// One explicit gateway route selection for Chat.
///
/// `automatic` preserves Wayfinder's routing policy. Every other value is a
/// configured gateway alias, not a provider credential or raw model id.
public struct ChatDestination: Identifiable, Hashable, Sendable {
    public static let automatic = ChatDestination(
        routeName: nil,
        title: "Automatic",
        detail: "Wayfinder chooses"
    )

    public let routeName: String?
    public let title: String
    public let defaultTitle: String
    public let detail: String
    public let providerName: String?
    public let isAvailable: Bool

    public var id: String { routeName ?? "auto" }
    public var gatewayModelValue: String { routeName ?? "auto" }
    public var isAutomatic: Bool { routeName == nil }
    public var isChatGPTAccount: Bool { providerName == "ChatGPT" }

    public init(
        routeName: String?,
        title: String,
        defaultTitle: String? = nil,
        detail: String,
        providerName: String? = nil,
        isAvailable: Bool = true
    ) {
        self.routeName = routeName
        self.title = title
        self.defaultTitle = defaultTitle ?? title
        self.detail = detail
        self.providerName = providerName
        self.isAvailable = isAvailable
    }

    public init(endpoint: EndpointDisplayStatus) {
        let presentation = Self.defaultPresentation(for: endpoint)
        self.init(
            routeName: endpoint.name,
            title: presentation.title,
            detail: presentation.detail,
            providerName: endpoint.providerName,
            isAvailable: endpoint.isChatDestinationAvailable
        )
    }

    public func withTitle(_ title: String) -> Self {
        Self(
            routeName: routeName,
            title: title,
            defaultTitle: defaultTitle,
            detail: detail,
            providerName: providerName,
            isAvailable: isAvailable
        )
    }

    public func withAvailability(_ isAvailable: Bool) -> Self {
        Self(
            routeName: routeName,
            title: title,
            defaultTitle: defaultTitle,
            detail: detail,
            providerName: providerName,
            isAvailable: isAvailable
        )
    }

    private static func defaultPresentation(
        for endpoint: EndpointDisplayStatus
    ) -> (title: String, detail: String) {
        if endpoint.providerName == "Apple Foundation Models" {
            return ("Apple Local", "Apple Foundation Models · This Mac")
        }

        if endpoint.providerName == "ChatGPT" {
            let modelTitle = endpoint.modelName?
                .replacingOccurrences(of: "gpt-", with: "GPT-")
                .replacingOccurrences(of: "-sol", with: " Sol")
            return (
                modelTitle ?? "ChatGPT",
                ["ChatGPT", modelTitle].compactMap { $0 }.joined(separator: " · ")
            )
        }

        return (
            endpoint.name,
            [endpoint.providerName, endpoint.modelName]
                .compactMap { $0 }
                .joined(separator: " · ")
        )
    }
}
