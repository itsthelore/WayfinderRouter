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
            SidebarHeader(turns: turns)

            SearchField(text: $searchText)
                .padding(.horizontal, 16)
                .padding(.bottom, 12)

            RouteFilterPicker(selected: $routeFilter, turns: turns)
                .padding(.horizontal, 16)
                .padding(.bottom, 16)

            HStack {
                Text("History")
                    .font(.caption2.weight(.semibold))
                    .textCase(.uppercase)
                    .tracking(0.7)
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
                Spacer()
                Text("\(visibleTurns.count)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            }
            .padding(.horizontal, 18)
            .padding(.top, 8)
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
        .frame(width: 252)
        .background(ChatWorkspaceChrome.sidebar)
    }
}

private struct SidebarHeader: View {
    let turns: [ChatTurn]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .fill(WayfinderTheme.local.opacity(0.13))
                    Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                        .foregroundStyle(WayfinderTheme.local)
                        .font(.system(size: 15, weight: .semibold))
                }
                .frame(width: 30, height: 30)

                VStack(alignment: .leading, spacing: 1) {
                    Text("Wayfinder")
                        .font(.headline.weight(.semibold))
                    Text("In-memory conversation")
                        .font(.caption)
                        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                }
                Spacer()
            }

            HStack(spacing: 12) {
                SidebarMetric(value: "\(turns.decisions.count)", label: "routed")
                SidebarMetric(value: localShareText, label: "local")
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 18)
        .padding(.bottom, 16)
    }

    private var localShareText: String {
        let decisions = turns.decisions
        guard !decisions.isEmpty else {
            return "0%"
        }
        let local = decisions.filter { $0.route == .local }.count
        return (Double(local) / Double(decisions.count)).percentText
    }
}

private struct SidebarMetric: View {
    let value: String
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.callout.monospacedDigit().weight(.semibold))
                .foregroundStyle(.primary)
            Text(label)
                .font(.caption2)
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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

private struct RouteFilterPicker: View {
    @Binding var selected: ChatRouteFilter
    let turns: [ChatTurn]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(ChatRouteFilter.allCases) { filter in
                Button {
                    selected = filter
                } label: {
                    VStack(spacing: 2) {
                        Text(filter.rawValue)
                            .font(.caption2.weight(.semibold))
                        Text("\(count(for: filter))")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 7)
                    .background(selected == filter ? ChatWorkspaceChrome.selectedFill : ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
                .buttonStyle(.plain)
                .foregroundStyle(selected == filter ? .primary : ChatWorkspaceChrome.secondaryText)
            }
        }
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
                Label("Routing", systemImage: "checkmark.shield")
                    .font(.caption)
                Spacer()
                Text("deterministic")
                    .font(.caption2.monospacedDigit().weight(.semibold))
                    .foregroundStyle(WayfinderTheme.local)
            }
        }
        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
        .padding(18)
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
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(selected ? ChatWorkspaceChrome.selectedFill : Color.clear, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
            .overlay(alignment: .leading) {
                if selected {
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(turn.response?.decision?.route.accentColor ?? WayfinderTheme.local)
                        .frame(width: 3)
                        .padding(.vertical, 8)
                }
            }
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 12)
    }
}

private struct SidebarEmptyState: View {
    let hasHistory: Bool

    var body: some View {
        VStack(spacing: 7) {
            Image(systemName: "magnifyingglass")
                .font(.callout)
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
            Text(hasHistory ? "No messages found" : "No messages yet")
                .font(.caption.weight(.medium))
            Text(hasHistory ? "Try another search or filter." : "Send a message to start this Chat.")
                .font(.caption2)
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 18)
        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
    }
}
