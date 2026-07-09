import Foundation

public struct RoutingFeature: Identifiable, Codable, Equatable, Sendable {
    public let id: UUID
    public let name: String
    public let value: String
    public let contribution: Double?

    public init(id: UUID = UUID(), name: String, value: String, contribution: Double?) {
        self.id = id
        self.name = name
        self.value = value
        self.contribution = contribution
    }

    public var label: String {
        name
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }
}
