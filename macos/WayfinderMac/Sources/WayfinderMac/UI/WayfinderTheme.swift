import SwiftUI

enum WayfinderTheme {
    static let local = Color(red: 0.07, green: 0.66, blue: 0.49)
    static let localTint = Color(red: 0.91, green: 0.97, blue: 0.95)
    static let cloud = Color(red: 0.79, green: 0.47, blue: 0.07)
    static let cloudTint = Color(red: 1.0, green: 0.95, blue: 0.89)
    static let selection = Color(red: 0.44, green: 0.39, blue: 0.96)
    static let selectionTint = Color(red: 0.93, green: 0.92, blue: 1.0)
    static let panel = Color(nsColor: .controlBackgroundColor).opacity(0.72)
    static let hairline = Color(nsColor: .separatorColor).opacity(0.5)
}

extension RouteTarget {
    var accentColor: Color {
        switch self {
        case .local:
            return WayfinderTheme.local
        case .cloud:
            return WayfinderTheme.cloud
        }
    }

    var softTint: Color {
        switch self {
        case .local:
            return WayfinderTheme.localTint
        case .cloud:
            return WayfinderTheme.cloudTint
        }
    }

    var label: String {
        switch self {
        case .local:
            return "LOCAL"
        case .cloud:
            return "CLOUD"
        }
    }

    var symbolName: String {
        switch self {
        case .local:
            return "desktopcomputer"
        case .cloud:
            return "cloud"
        }
    }
}

extension Double {
    var percentText: String {
        self.formatted(.percent.precision(.fractionLength(0)))
    }

    var scoreText: String {
        self.formatted(.number.precision(.fractionLength(2)))
    }
}

extension Decimal {
    var currencyText: String {
        let value = NSDecimalNumber(decimal: self).doubleValue
        return value.formatted(.currency(code: "USD").precision(.fractionLength(2)))
    }
}
