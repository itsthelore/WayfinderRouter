import SwiftUI

public struct ChatSidebarView: View {
    let turns: [ChatTurn]
    let visibleTurns: [ChatTurn]
    @Binding var selectedTurnID: UUID?
    @Binding var routeFilter: ChatRouteFilter
    @Binding var searchText: String
    let searchFocusRequest: Int
    let onSelectTurn: () -> Void

    public init(
        turns: [ChatTurn],
        visibleTurns: [ChatTurn],
        selectedTurnID: Binding<UUID?>,
        routeFilter: Binding<ChatRouteFilter>,
        searchText: Binding<String>,
        searchFocusRequest: Int = 0,
        onSelectTurn: @escaping () -> Void = {}
    ) {
        self.turns = turns
        self.visibleTurns = visibleTurns
        self._selectedTurnID = selectedTurnID
        self._routeFilter = routeFilter
        self._searchText = searchText
        self.searchFocusRequest = searchFocusRequest
        self.onSelectTurn = onSelectTurn
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SidebarHeader()

            SearchField(text: $searchText, focusRequest: searchFocusRequest)
                .padding(.horizontal, 12)
                .padding(.bottom, 14)

            HStack {
                Text("Conversation")
                    .font(.caption2.weight(.semibold))
                    .textCase(.uppercase)
                    .tracking(0.7)
                    .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
                Spacer()
                RouteFilterMenu(selected: $routeFilter, turns: turns)
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 8)

            if visibleTurns.isEmpty {
                SidebarEmptyState(hasHistory: !turns.isEmpty)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    .padding(.top, 24)
            } else {
                List(selection: turnSelection) {
                    ForEach(visibleTurns) { turn in
                        SidebarTurnRow(
                            turn: turn,
                            isSelected: turn.id == selectedTurnID
                        )
                            .tag(turn.id)
                    }
                }
                .listStyle(.sidebar)
                .scrollContentBackground(.hidden)
            }

            SidebarStatusFooter(turns: turns)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(ChatWorkspaceChrome.sidebar)
    }

    private var turnSelection: Binding<UUID?> {
        Binding(
            get: { selectedTurnID },
            set: { turnID in
                selectedTurnID = turnID
                if turnID != nil {
                    onSelectTurn()
                }
            }
        )
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
            Text("Current chat")
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
    let focusRequest: Int
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
                .accessibilityHidden(true)
            TextField("Search turns", text: $text)
                .textFieldStyle(.plain)
                .font(.caption)
                .focused($focused)
                .accessibilityLabel("Search conversation turns")
            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(ChatWorkspaceChrome.tertiaryText)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .stroke(ChatWorkspaceChrome.border, lineWidth: 1)
        )
        .onChange(of: focusRequest) {
            focused = true
        }
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
        .accessibilityLabel("Route filter")
        .accessibilityValue(selected.rawValue)
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
                Label("In memory", systemImage: "memorychip")
                    .font(.caption2)
                Spacer()
                Text(turnCountText)
                    .font(.caption2.monospacedDigit())
            }
        }
        .foregroundStyle(ChatWorkspaceChrome.secondaryText)
        .padding(18)
    }

    private var turnCountText: String {
        turns.count == 1 ? "1 turn" : "\(turns.count) turns"
    }

}

private struct SidebarTurnRow: View {
    let turn: ChatTurn
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 9) {
            Circle()
                .fill(statusColor)
                .frame(width: 7, height: 7)

            VStack(alignment: .leading, spacing: 2) {
                Text(turn.prompt.text)
                    .lineLimit(1)
                    .foregroundStyle(.primary)
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                    .lineLimit(1)
            }

            Spacer(minLength: 4)
        }
        .font(.caption)
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(turn.prompt.text), \(subtitle)")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var subtitle: String {
        let time = turn.prompt.createdAt.formatted(date: .omitted, time: .shortened)
        guard let response = turn.response else {
            return "Waiting for route · \(time)"
        }

        switch response.state {
        case .streaming:
            return "Routing · \(time)"
        case .failed:
            return "Failed · \(time)"
        case .stopped:
            return "Stopped · \(time)"
        case .complete:
            return response.decision == nil ? "No route data · \(time)" : time
        }
    }

    private var statusColor: Color {
        switch turn.response?.state {
        case .failed:
            return .red
        case .stopped:
            return ChatWorkspaceChrome.secondaryText
        case .streaming, .none:
            return WayfinderTheme.local
        case .complete:
            return turn.response?.decision == nil
                ? ChatWorkspaceChrome.tertiaryText
                : ChatWorkspaceChrome.secondaryText
        }
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
