import SwiftUI

enum ChatWorkspaceChrome {
    static let sidebar = Color(red: 0.115, green: 0.13, blue: 0.135)
    static let canvas = Color(red: 0.072, green: 0.076, blue: 0.074)
    static let panel = Color(red: 0.135, green: 0.14, blue: 0.138)
    static let panelRaised = Color(red: 0.175, green: 0.178, blue: 0.172)
    static let border = Color.white.opacity(0.075)
    static let rowHover = Color.white.opacity(0.06)
    static let secondaryText = Color.white.opacity(0.58)
    static let tertiaryText = Color.white.opacity(0.36)
    static let mutedFill = Color.white.opacity(0.045)
    static let selectedFill = Color.white.opacity(0.085)
}

public enum ChatRouteFilter: String, CaseIterable, Identifiable {
    case all = "All"
    case local = "Local"
    case cloud = "Cloud"

    public var id: String { rawValue }

    public func includes(_ turn: ChatTurn) -> Bool {
        guard let route = turn.response?.decision?.route else {
            return self == .all
        }

        switch self {
        case .all:
            return true
        case .local:
            return route == .local
        case .cloud:
            return route == .cloud
        }
    }
}

extension Array where Element == ChatTurn {
    func filtered(by filter: ChatRouteFilter, searchText: String) -> [ChatTurn] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return self.filter { turn in
            filter.includes(turn)
                && (query.isEmpty
                    || turn.prompt.text.lowercased().contains(query)
                    || (turn.response?.decision?.provider.lowercased().contains(query) ?? false)
                    || (turn.response?.decision?.route.rawValue.lowercased().contains(query) ?? false))
        }
    }

    var decisions: [RoutingDecision] {
        compactMap { $0.response?.decision }
    }
}

extension RoutingDecision {
    var routeSummary: String {
        switch route {
        case .local:
            return "Kept local"
        case .cloud:
            return "Sent to cloud"
        }
    }

    var routeReasonTitle: String {
        switch route {
        case .local:
            return "Low complexity prompt"
        case .cloud:
            return "Higher complexity prompt"
        }
    }
}
