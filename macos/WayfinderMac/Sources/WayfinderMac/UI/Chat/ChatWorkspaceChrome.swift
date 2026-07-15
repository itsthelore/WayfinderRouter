import SwiftUI

enum ChatWorkspaceChrome {
    static let sidebar = Color(nsColor: .underPageBackgroundColor)
    static let canvas = Color(nsColor: .windowBackgroundColor)
    static let panel = Color(nsColor: .controlBackgroundColor)
    static let panelRaised = Color(nsColor: .unemphasizedSelectedContentBackgroundColor)
    static let border = Color(nsColor: .separatorColor).opacity(0.7)
    static let rowHover = Color.primary.opacity(0.06)
    static let secondaryText = Color(nsColor: .secondaryLabelColor)
    static let tertiaryText = Color(nsColor: .tertiaryLabelColor)
    static let mutedFill = Color.primary.opacity(0.045)
    static let selectedFill = Color(nsColor: .unemphasizedSelectedContentBackgroundColor)
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
