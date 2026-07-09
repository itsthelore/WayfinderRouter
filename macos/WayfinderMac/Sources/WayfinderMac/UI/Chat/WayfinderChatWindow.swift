import SwiftUI

public struct WayfinderChatWindow: View {
    @EnvironmentObject private var appState: AppState
    @State private var selectedDecisionID: UUID?
    @State private var routeFilter: ChatRouteFilter = .all
    @State private var searchText = ""

    public init() {}

    public var body: some View {
        let turns = ChatTurn.make(from: appState.chatMessages)
        let visibleTurns = turns.filtered(by: routeFilter, searchText: searchText)

        HStack(spacing: 0) {
            ChatSidebarView(
                turns: turns,
                visibleTurns: visibleTurns,
                selectedDecisionID: $selectedDecisionID,
                routeFilter: $routeFilter,
                searchText: $searchText,
                onNewRoute: startNewRoute
            )

            Divider()

            VStack(spacing: 0) {
                ChatToolbar(
                    turnCount: routedTurnCount,
                    visibleCount: visibleTurns.count,
                    selectedDecision: selectedDecision(in: turns),
                    routeFilter: routeFilter,
                    onSelectLatest: { selectLatestDecision(in: visibleTurns) }
                )
                Divider()
                ChatConversationView(
                    turns: visibleTurns,
                    selectedDecisionID: $selectedDecisionID
                )
                Divider()
                ChatComposerView(
                    draft: $appState.chatDraft,
                    isSending: appState.isSendingMessage,
                    canSend: appState.canSendMessage,
                    onSend: appState.sendChatDraft
                )
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            RoutingOutputsPanel(
                decision: selectedDecision(in: turns),
                turn: selectedTurn(in: turns)
            )
        }
        .frame(minWidth: 1180, minHeight: 740)
        .background(ChatWorkspaceChrome.canvas)
        .preferredColorScheme(.dark)
        .onAppear {
            selectedDecisionID = selectedDecisionID ?? latestDecision(in: turns)?.id
        }
        .onChange(of: appState.chatMessages.count) { _ in
            let updatedTurns = ChatTurn.make(from: appState.chatMessages)
            let visible = updatedTurns.filtered(by: routeFilter, searchText: searchText)
            selectedDecisionID = latestDecision(in: visible)?.id ?? selectedDecisionID
        }
        .onChange(of: routeFilter) { _ in
            selectValidDecision(in: visibleTurns)
        }
        .onChange(of: searchText) { _ in
            selectValidDecision(in: visibleTurns)
        }
    }

    private var routedTurnCount: Int {
        appState.chatMessages.filter { $0.role == .router && $0.decision != nil }.count
    }

    private func startNewRoute() {
        appState.chatDraft = ""
        selectedDecisionID = nil
    }

    private func selectLatestDecision(in turns: [ChatTurn]) {
        selectedDecisionID = latestDecision(in: turns)?.id
    }

    private func selectValidDecision(in turns: [ChatTurn]) {
        let visibleDecisionIDs = Set(turns.compactMap { $0.response?.decision?.id })
        if let selectedDecisionID, visibleDecisionIDs.contains(selectedDecisionID) {
            return
        }
        selectedDecisionID = latestDecision(in: turns)?.id
    }

    private func selectedDecision(in turns: [ChatTurn]) -> RoutingDecision? {
        guard let selectedDecisionID else {
            return nil
        }
        let decisions = turns.compactMap { $0.response?.decision }
        return decisions.first { $0.id == selectedDecisionID }
    }

    private func selectedTurn(in turns: [ChatTurn]) -> ChatTurn? {
        guard let selectedDecisionID else {
            return nil
        }
        return turns.first { $0.response?.decision?.id == selectedDecisionID }
    }

    private func latestDecision(in turns: [ChatTurn]) -> RoutingDecision? {
        turns.compactMap { $0.response?.decision }.last
    }
}

private struct ChatToolbar: View {
    let turnCount: Int
    let visibleCount: Int
    let selectedDecision: RoutingDecision?
    let routeFilter: ChatRouteFilter
    let onSelectLatest: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text("Route Preview")
                        .font(.title3.weight(.semibold))
                    Text("Mock data")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(WayfinderTheme.selection)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(WayfinderTheme.selection.opacity(0.12), in: Capsule())
                    if let selectedDecision {
                        Text(selectedDecision.routeSummary)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(selectedDecision.route.accentColor)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(selectedDecision.route.accentColor.opacity(0.12), in: Capsule())
                    }
                }
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            }
            Spacer()
            HStack(spacing: 6) {
                Image(systemName: "bolt.horizontal")
                    .font(.caption)
                Text("<1 ms")
                    .font(.caption.monospacedDigit())
            }
            .foregroundStyle(.secondary)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(ChatWorkspaceChrome.mutedFill, in: Capsule())

            Button(action: onSelectLatest) {
                Label("Latest", systemImage: "clock.arrow.circlepath")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help("Select the latest visible routed turn")
        }
        .buttonStyle(.borderless)
        .padding(.horizontal, 22)
        .padding(.vertical, 15)
        .background(ChatWorkspaceChrome.canvas)
    }

    private var subtitle: String {
        let base = "\(turnCount) preview turns · local mock scorer"
        guard routeFilter != .all else {
            return base
        }
        return "\(visibleCount) \(routeFilter.rawValue.lowercased()) turns · \(base)"
    }
}
