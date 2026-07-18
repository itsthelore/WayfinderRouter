import SwiftUI

public struct WayfinderChatWindow: View {
    @EnvironmentObject private var appState: AppState
    @State private var selectedDecisionID: UUID?
    @State private var routeFilter: ChatRouteFilter = .all
    @State private var searchText = ""
    @State private var showsSidebar = true
    @State private var showsInspector = false

    public init() {}

    public var body: some View {
        let turns = ChatTurn.make(from: appState.chatMessages)
        let visibleTurns = turns.filtered(by: routeFilter, searchText: searchText)

        HStack(spacing: 0) {
            if showsSidebar {
                ChatSidebarView(
                    turns: turns,
                    visibleTurns: visibleTurns,
                    selectedDecisionID: $selectedDecisionID,
                    routeFilter: $routeFilter,
                    searchText: $searchText
                )

                Divider()
            }

            VStack(spacing: 0) {
                ChatToolbar(
                    title: chatTitle(in: turns),
                    turnCount: routedTurnCount,
                    visibleCount: visibleTurns.count,
                    selectedDecision: selectedDecision(in: turns),
                    routeFilter: routeFilter,
                    showsSidebar: showsSidebar,
                    showsInspector: showsInspector,
                    onToggleSidebar: { showsSidebar.toggle() },
                    onToggleInspector: { showsInspector.toggle() },
                    canRetry: appState.canRetryChat,
                    canClear: appState.canClearChat,
                    onRetry: appState.retryLastChatTurn,
                    onClear: appState.clearChat
                )
                Divider()
                ChatConversationView(
                    turns: visibleTurns,
                    hasHistory: !turns.isEmpty,
                    selectedDecisionID: $selectedDecisionID,
                    onOpenDecision: { decision in
                        selectedDecisionID = decision.id
                        showsInspector = true
                    }
                )
                ChatComposerView(
                    draft: $appState.chatDraft,
                    isSending: appState.isSendingMessage,
                    canSend: appState.canSendMessage,
                    onSend: appState.sendChatDraft,
                    onStop: appState.stopChatResponse
                )
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            if showsInspector, let decision = selectedDecision(in: turns) {
                Divider()
                RoutingOutputsPanel(
                    decision: decision,
                    turn: selectedTurn(in: turns),
                    onClose: { showsInspector = false }
                )
            }
        }
        .frame(minWidth: 1_040, minHeight: 620)
        .background(ChatWorkspaceChrome.canvas)
        .onAppear {
            selectedDecisionID = selectedDecisionID ?? latestDecision(in: turns)?.id
        }
        .onChange(of: appState.chatMessages.count) {
            let updatedTurns = ChatTurn.make(from: appState.chatMessages)
            let visible = updatedTurns.filtered(by: routeFilter, searchText: searchText)
            selectedDecisionID = latestDecision(in: visible)?.id ?? selectedDecisionID
        }
        .onChange(of: visibleTurns.compactMap { $0.response?.decision?.id }) {
            selectedDecisionID = latestDecision(in: visibleTurns)?.id ?? selectedDecisionID
        }
        .onChange(of: routeFilter) {
            selectValidDecision(in: visibleTurns)
        }
        .onChange(of: searchText) {
            selectValidDecision(in: visibleTurns)
        }
    }

    private var routedTurnCount: Int {
        appState.chatMessages.filter { $0.role == .assistant && $0.decision != nil }.count
    }

    private func chatTitle(in turns: [ChatTurn]) -> String {
        guard let firstPrompt = turns.first?.prompt.text,
              !firstPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return "New chat"
        }
        return firstPrompt
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
    let title: String
    let turnCount: Int
    let visibleCount: Int
    let selectedDecision: RoutingDecision?
    let routeFilter: ChatRouteFilter
    let showsSidebar: Bool
    let showsInspector: Bool
    let onToggleSidebar: () -> Void
    let onToggleInspector: () -> Void
    let canRetry: Bool
    let canClear: Bool
    let onRetry: () -> Void
    let onClear: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button(action: onToggleSidebar) {
                Image(systemName: "sidebar.left")
            }
            .help(showsSidebar ? "Hide turn navigator" : "Show turn navigator")

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(title)
                        .font(.headline.weight(.semibold))
                        .lineLimit(1)
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
            if canRetry {
                Button(action: onRetry) {
                    Image(systemName: "arrow.clockwise")
                }
                .help("Retry the last response")
            }
            Button(action: onToggleInspector) {
                Image(systemName: "sidebar.right")
                    .symbolVariant(showsInspector ? .fill : .none)
            }
            .disabled(selectedDecision == nil)
            .help(showsInspector ? "Hide route details" : "Show route details")
            Button(action: onClear) {
                Image(systemName: "square.and.pencil")
            }
            .disabled(!canClear)
            .help("Clear this in-memory conversation")
        }
        .buttonStyle(.borderless)
        .controlSize(.small)
        .padding(.horizontal, 18)
        .frame(height: 54)
        .background(ChatWorkspaceChrome.canvas)
    }

    private var subtitle: String {
        let base = "\(turnCount) routed \(turnCount == 1 ? "turn" : "turns")"
        guard routeFilter != .all else {
            return base
        }
        return "\(visibleCount) \(routeFilter.rawValue.lowercased()) turns · \(base)"
    }
}
