import SwiftUI

public struct ChatSidebarView: View {
    let turns: [ChatTurn]
    let visibleTurns: [ChatTurn]
    @Binding var selectedDecisionID: UUID?
    @Binding var routeFilter: ChatRouteFilter
    @Binding var searchText: String

    public init(
        turns: [ChatTurn],
        visibleTurns: [ChatTurn],
        selectedDecisionID: Binding<UUID?>,
        routeFilter: Binding<ChatRouteFilter>,
        searchText: Binding<String>
    ) {
        self.turns = turns
        self.visibleTurns = visibleTurns
        self._selectedDecisionID = selectedDecisionID
        self._routeFilter = routeFilter
        self._searchText = searchText
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SidebarHeader()

            SearchField(text: $searchText)
                .padding(.horizontal, 12)
                .padding(.bottom, 14)

            HStack {
                Text("Turns")
                    .font(.caption2.weight(.semibold))
                    .textCase(.uppercase)
                    .tracking(0.7)
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
                Spacer()
                RouteFilterMenu(selected: $routeFilter, turns: turns)
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 8)

            ScrollView {
                LazyVStack(spacing: 3) {
                    if visibleTurns.isEmpty {
                        SidebarEmptyState(hasHistory: !turns.isEmpty)
                            .padding(.top, 24)
                    } else {
                        ForEach(visibleTurns) { turn in
                            SidebarTurnRow(
                                turn: turn,
                                selected: turn.response?.decision?.id == selectedDecisionID
                            ) {
                                if let decision = turn.response?.decision {
                                    selectedDecisionID = decision.id
                                }
                            }
                        }
                    }
                }
                .padding(.bottom, 8)
            }

            Spacer()

            SidebarStatusFooter(turns: turns)
        }
        .frame(width: ChatWorkspaceChrome.sidebarWidth)
        .background(ChatWorkspaceChrome.sidebar)
    }
}

private struct SidebarHeader: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text("Wayfinder")
                    .font(.headline.weight(.semibold))
                Spacer()
                Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                    .foregroundStyle(WayfinderTheme.local)
                    .font(.system(size: 14, weight: .semibold))
            }
            Text("In-memory chat")
                .font(.caption)
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
        }
        .padding(.horizontal, 14)
        .padding(.top, 15)
        .padding(.bottom, 14)
    }

}

private struct SearchField: View {
    @Binding var text: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            TextField("Search Chat", text: $text)
                .textFieldStyle(.plain)
                .font(.caption)
            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .stroke(ChatWorkspaceChrome.border, lineWidth: 1)
        )
    }
}

private struct RouteFilterMenu: View {
    @Binding var selected: ChatRouteFilter
    let turns: [ChatTurn]

    var body: some View {
        Menu {
            ForEach(ChatRouteFilter.allCases) { filter in
                Button {
                    selected = filter
                } label: {
                    Text("\(filter.rawValue) (\(count(for: filter)))")
                }
            }
        } label: {
            HStack(spacing: 4) {
                Text(selected.rawValue)
                Text("\(count(for: selected))")
                    .monospacedDigit()
            }
            .font(.caption2)
            .foregroundStyle(ChatWorkspaceChrome.secondaryText)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }

    private func count(for filter: ChatRouteFilter) -> Int {
        turns.filter { filter.includes($0) }.count
    }
}

private struct SidebarStatusFooter: View {
    let turns: [ChatTurn]

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Divider()
                .overlay(ChatWorkspaceChrome.border)
            HStack {
                Label("\(turns.decisions.count) routed", systemImage: "checkmark.shield")
                    .font(.caption2)
                Spacer()
                Text(localShareText)
                    .font(.caption2.monospacedDigit().weight(.semibold))
                    .foregroundStyle(turns.decisions.isEmpty ? ChatWorkspaceChrome.tertiaryText : WayfinderTheme.local)
            }
        }
        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
        .padding(18)
    }

    private var localShareText: String {
        let decisions = turns.decisions
        guard !decisions.isEmpty else {
            return "In memory"
        }
        let local = decisions.filter { $0.route == .local }.count
        return "\((Double(local) / Double(decisions.count)).percentText) local"
    }

}

private struct SidebarTurnRow: View {
    let turn: ChatTurn
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Circle()
                    .fill(turn.response?.decision?.route.accentColor ?? ChatWorkspaceChrome.secondaryText)
                    .frame(width: 7, height: 7)

                VStack(alignment: .leading, spacing: 2) {
                    Text(turn.prompt.text)
                        .lineLimit(1)
                        .foregroundStyle(.primary)
                    if let decision = turn.response?.decision {
                        Text("\(decision.routeSummary) · \(decision.score.scoreText) · \(turn.prompt.createdAt.formatted(date: .omitted, time: .shortened))")
                            .font(.caption2)
                            .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                    }
                }

                Spacer(minLength: 4)
            }
            .font(.caption)
            .padding(.horizontal, 9)
            .padding(.vertical, 8)
            .background(selected ? ChatWorkspaceChrome.selectedFill : Color.clear, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 7)
    }
}

private struct SidebarEmptyState: View {
    let hasHistory: Bool

    var body: some View {
        VStack(spacing: 6) {
            if hasHistory {
                Image(systemName: "magnifyingglass")
                    .font(.callout)
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }
            Text(hasHistory ? "No turns found" : "No turns yet")
                .font(.caption.weight(.medium))
            if hasHistory {
                Text("Try another search or filter.")
                    .font(.caption2)
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 18)
        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
    }
}
