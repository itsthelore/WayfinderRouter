public struct ProviderConfig: Equatable, Sendable {
    public let id: String
    public let displayName: String
    public let route: RouteTarget

    public init(id: String, displayName: String, route: RouteTarget) {
        self.id = id
        self.displayName = displayName
        self.route = route
    }
}
