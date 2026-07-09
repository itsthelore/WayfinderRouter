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
    public let localRouteCount: Int?
    public let cloudRouteCount: Int?
    public let totalTurns: Int?
    public let savedToday: Decimal
    public let savedLast30Days: Decimal
    public let cloudSpendToday: Decimal
    public let percentVsAlwaysCloud: Double
    public let isPriced: Bool
    public let hasSavings: Bool
    public let savedTodayDisplay: String
    public let savedLast30DaysDisplay: String
    public let averageRoutingTimeMilliseconds: Double
    public let updatedAt: Date
    public let isRunning: Bool

    public init(
        localPercent: Double,
        cloudPercent: Double,
        localRouteCount: Int? = nil,
        cloudRouteCount: Int? = nil,
        totalTurns: Int? = nil,
        savedToday: Decimal,
        savedLast30Days: Decimal,
        cloudSpendToday: Decimal,
        percentVsAlwaysCloud: Double,
        isPriced: Bool = true,
        hasSavings: Bool = true,
        savedTodayDisplay: String? = nil,
        savedLast30DaysDisplay: String? = nil,
        averageRoutingTimeMilliseconds: Double,
        updatedAt: Date,
        isRunning: Bool
    ) {
        self.localPercent = localPercent
        self.cloudPercent = cloudPercent
        self.localRouteCount = localRouteCount
        self.cloudRouteCount = cloudRouteCount
        self.totalTurns = totalTurns
        self.savedToday = savedToday
        self.savedLast30Days = savedLast30Days
        self.cloudSpendToday = cloudSpendToday
        self.percentVsAlwaysCloud = percentVsAlwaysCloud
        self.isPriced = isPriced
        self.hasSavings = hasSavings
        self.savedTodayDisplay = savedTodayDisplay ?? savedToday.currencyText
        self.savedLast30DaysDisplay = savedLast30DaysDisplay ?? savedLast30Days.currencyText
        self.averageRoutingTimeMilliseconds = averageRoutingTimeMilliseconds
        self.updatedAt = updatedAt
        self.isRunning = isRunning
    }
}

public struct GatewayOverview: Equatable, Sendable {
    public let gateway: GatewayDisplayState
    public let hosted: HostedDisplayState
    public let routingStats: RoutingStats
    public let updatedAt: Date

    public init(
        gateway: GatewayDisplayState,
        hosted: HostedDisplayState,
        routingStats: RoutingStats,
        updatedAt: Date
    ) {
        self.gateway = gateway
        self.hosted = hosted
        self.routingStats = routingStats
        self.updatedAt = updatedAt
    }
}

public enum GatewayDisplayState: Equatable, Sendable {
    case running(detail: String)
    case degraded(detail: String)
    case offline(detail: String)
    case stopped(detail: String)
    case unreachable(detail: String)
    case notInstalled(detail: String)

    public var title: String {
        switch self {
        case .running:
            return "Running"
        case .degraded:
            return "Degraded"
        case .offline:
            return "Offline"
        case .stopped:
            return "Stopped"
        case .unreachable:
            return "Unreachable"
        case .notInstalled:
            return "Not Installed"
        }
    }

    public var detail: String {
        switch self {
        case .running(let detail),
             .degraded(let detail),
             .offline(let detail),
             .stopped(let detail),
             .unreachable(let detail),
             .notInstalled(let detail):
            return detail
        }
    }

    public var isRunning: Bool {
        switch self {
        case .running, .degraded, .offline:
            return true
        case .stopped, .unreachable, .notInstalled:
            return false
        }
    }
}

public enum HostedDisplayState: Equatable, Sendable {
    case ready(detail: String)
    case checkKeys(detail: String)
    case disabled(detail: String)
    case noModels(detail: String)
    case unavailable(detail: String)

    public var title: String {
        switch self {
        case .ready:
            return "Ready"
        case .checkKeys:
            return "Check Keys"
        case .disabled:
            return "Disabled"
        case .noModels:
            return "No Models"
        case .unavailable:
            return "Unavailable"
        }
    }

    public var detail: String {
        switch self {
        case .ready(let detail),
             .checkKeys(let detail),
             .disabled(let detail),
             .noModels(let detail),
             .unavailable(let detail):
            return detail
        }
    }
}
