import SwiftUI

enum ChatWorkspaceChrome {
    static let sidebar = Color(nsColor: .windowBackgroundColor)
    static let inspector = Color(nsColor: .windowBackgroundColor)
    static let canvas = Color(nsColor: .textBackgroundColor)
    static let composer = Color(nsColor: .textBackgroundColor)
    static let border = Color(nsColor: .separatorColor).opacity(0.7)
    static let secondaryText = Color(nsColor: .secondaryLabelColor)
    static let tertiaryText = Color(nsColor: .tertiaryLabelColor)
    static let mutedFill = Color.primary.opacity(0.045)

    static let sidebarMinimumWidth: CGFloat = 210
    static let sidebarWidth: CGFloat = 220
    static let sidebarMaximumWidth: CGFloat = 260
    static let inspectorMinimumWidth: CGFloat = 260
    static let inspectorWidth: CGFloat = 296
    static let inspectorMaximumWidth: CGFloat = 340
    static let conversationWidth: CGFloat = 760
    static let composerWidth: CGFloat = 780
    static let initialWindowWidth: CGFloat = 1_180
    static let initialWindowHeight: CGFloat = 720
    static let minimumWindowWidth: CGFloat = 900
    static let minimumWindowHeight: CGFloat = 580
    static let showsInspectorByDefault = false
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

struct ChatWorkspaceContent: Equatable {
    let transcriptTurns: [ChatTurn]
    let navigatorTurns: [ChatTurn]

    init(turns: [ChatTurn], routeFilter: ChatRouteFilter, searchText: String) {
        transcriptTurns = turns
        navigatorTurns = turns.filtered(by: routeFilter, searchText: searchText)
    }
}

enum ChatWorkspaceSelectionPolicy {
    static func resolvedTurnID(
        current: UUID?,
        followsLatest: Bool,
        turnIDs: [UUID]
    ) -> UUID? {
        guard let latest = turnIDs.last else {
            return nil
        }
        guard let current else {
            return latest
        }
        if followsLatest || !turnIDs.contains(current) {
            return latest
        }
        return current
    }
}

enum ChatScrollFollowPolicy {
    static func shouldFollowLatest(
        isNearBottom: Bool,
        selectedTurnID: UUID?,
        latestTurnID: UUID?
    ) -> Bool {
        isNearBottom && latestTurnID != nil && selectedTurnID == latestTurnID
    }
}

extension Array where Element == ChatTurn {
    func filtered(by filter: ChatRouteFilter, searchText: String) -> [ChatTurn] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return self.filter { turn in
            filter.includes(turn)
                && (query.isEmpty
                    || turn.prompt.text.lowercased().contains(query)
                    || (turn.response?.text.lowercased().contains(query) ?? false)
                    || (turn.response?.decision?.provider.lowercased().contains(query) ?? false)
                    || (turn.response?.decision?.route.rawValue.lowercased().contains(query) ?? false))
        }
    }
}

extension RoutingDecision {
    var routeSummary: String {
        switch route {
        case .local:
            return "Local route"
        case .cloud:
            return "Cloud route"
        }
    }
}
