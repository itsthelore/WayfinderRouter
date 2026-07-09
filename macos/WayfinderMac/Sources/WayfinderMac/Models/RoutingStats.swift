import Foundation

public enum StatsRange: String, CaseIterable, Identifiable, Codable, Sendable {
    case today = "Today"
    case sevenDays = "7d"
    case thirtyDays = "30d"

    public var id: String { rawValue }
}

public struct RoutingStats: Codable, Equatable, Sendable {
    public let localPercent: Double
    public let cloudPercent: Double
    public let savedToday: Decimal
    public let savedLast30Days: Decimal
    public let cloudSpendToday: Decimal
    public let percentVsAlwaysCloud: Double
    public let averageRoutingTimeMilliseconds: Double
    public let updatedAt: Date
    public let isRunning: Bool

    public init(
        localPercent: Double,
        cloudPercent: Double,
        savedToday: Decimal,
        savedLast30Days: Decimal,
        cloudSpendToday: Decimal,
        percentVsAlwaysCloud: Double,
        averageRoutingTimeMilliseconds: Double,
        updatedAt: Date,
        isRunning: Bool
    ) {
        self.localPercent = localPercent
        self.cloudPercent = cloudPercent
        self.savedToday = savedToday
        self.savedLast30Days = savedLast30Days
        self.cloudSpendToday = cloudSpendToday
        self.percentVsAlwaysCloud = percentVsAlwaysCloud
        self.averageRoutingTimeMilliseconds = averageRoutingTimeMilliseconds
        self.updatedAt = updatedAt
        self.isRunning = isRunning
    }
}
