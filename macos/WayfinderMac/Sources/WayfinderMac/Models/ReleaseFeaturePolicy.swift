public enum AppFeature: Hashable, Sendable {
    case chat
}

public enum FeatureAvailability: Equatable, Sendable {
    case available
    case blocked(reason: String)

    public var isAvailable: Bool {
        if case .available = self {
            return true
        }
        return false
    }

    public var blockedReason: String? {
        if case let .blocked(reason) = self {
            return reason
        }
        return nil
    }
}

public struct ReleaseFeaturePolicy: Equatable, Sendable {
    private let availability: [AppFeature: FeatureAvailability]

    public init(availability: [AppFeature: FeatureAvailability]) {
        self.availability = availability
    }

    public subscript(feature: AppFeature) -> FeatureAvailability {
        availability[feature] ?? .blocked(reason: "This feature is unavailable in this release.")
    }

    public static let v1 = ReleaseFeaturePolicy(
        availability: [
            .chat: .blocked(reason: "Chat is unavailable in this release.")
        ]
    )

    public static let current = v1
}

struct ChatPopoverRowModel: Equatable {
    let isEnabled: Bool
    let trailingText: String?
    let showsChevron: Bool
    let accessibilityLabel: String
    let accessibilityHint: String

    init(availability: FeatureAvailability) {
        switch availability {
        case .available:
            self.isEnabled = true
            self.trailingText = nil
            self.showsChevron = true
            self.accessibilityLabel = "Chat"
            self.accessibilityHint = "Opens the Chat window."
        case let .blocked(reason):
            self.isEnabled = false
            self.trailingText = "Coming later"
            self.showsChevron = false
            self.accessibilityLabel = "Chat, Coming later"
            self.accessibilityHint = reason
        }
    }
}
