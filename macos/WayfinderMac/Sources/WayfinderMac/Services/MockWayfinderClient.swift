import Foundation

public struct MockWayfinderClient: WayfinderClient {
    private let localClient = LocalWayfinderClient()

    public init() {}

    public func route(prompt: String) async throws -> RoutingDecision {
        try await analyse(prompt: prompt)
    }

    public func analyse(prompt: String) async throws -> RoutingDecision {
        try await Task.sleep(nanoseconds: 120_000_000)
        return try await localClient.analyse(prompt: prompt)
    }

    public func loadStats(range: StatsRange) async throws -> RoutingStats {
        try await Task.sleep(nanoseconds: 80_000_000)
        let local: Double
        let cloud: Double
        switch range {
        case .today:
            local = 0.68
            cloud = 0.32
        case .sevenDays:
            local = 0.71
            cloud = 0.29
        case .thirtyDays:
            local = 0.74
            cloud = 0.26
        }

        return RoutingStats(
            localPercent: local,
            cloudPercent: cloud,
            savedToday: Decimal(string: "0.01") ?? 0,
            savedLast30Days: Decimal(string: "0.01") ?? 0,
            cloudSpendToday: Decimal(string: "0.00") ?? 0,
            percentVsAlwaysCloud: 0.29,
            averageRoutingTimeMilliseconds: 0.82,
            updatedAt: Date(),
            isRunning: true
        )
    }
}
