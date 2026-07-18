import SwiftUI

enum ChatWorkspaceChrome {
    static let sidebar = Color(nsColor: .underPageBackgroundColor)
    static let inspector = Color(nsColor: .underPageBackgroundColor)
    static let canvas = Color(nsColor: .windowBackgroundColor)
    static let composer = Color(nsColor: .textBackgroundColor)
    static let border = Color(nsColor: .separatorColor).opacity(0.7)
    static let secondaryText = Color(nsColor: .secondaryLabelColor)
    static let tertiaryText = Color(nsColor: .tertiaryLabelColor)
    static let mutedFill = Color.primary.opacity(0.045)

    static let sidebarMinimumWidth: CGFloat = 210
    static let sidebarWidth: CGFloat = 232
    static let sidebarMaximumWidth: CGFloat = 280
    static let inspectorMinimumWidth: CGFloat = 290
    static let inspectorWidth: CGFloat = 320
    static let inspectorMaximumWidth: CGFloat = 390
    static let conversationWidth: CGFloat = 760
    static let composerWidth: CGFloat = 780
    static let initialWindowWidth: CGFloat = 1_320
    static let initialWindowHeight: CGFloat = 780
    static let minimumWindowWidth: CGFloat = 940
    static let minimumWindowHeight: CGFloat = 620
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
