public struct PromptRouter: WayfinderClient {
    private let client: any WayfinderClient

    public init(client: any WayfinderClient) {
        self.client = client
    }

    public func route(prompt: String) async throws -> RoutingDecision {
        try await client.route(prompt: prompt)
    }

    public func analyse(prompt: String) async throws -> RoutingDecision {
        try await client.route(prompt: prompt)
    }

    public func loadStats(range: StatsRange) async throws -> RoutingStats {
        try await client.loadStats(range: range)
    }
}
