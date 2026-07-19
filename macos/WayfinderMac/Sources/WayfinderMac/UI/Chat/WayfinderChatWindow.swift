import SwiftUI

public struct WayfinderChatWindow: View {
    @EnvironmentObject private var appState: AppState
    @State private var selectedTurnID: UUID?
    @State private var routeFilter: ChatRouteFilter = .all
    @State private var searchText = ""
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    @State private var showsInspector = true
    @State private var followsLatestTurn = true
    @State private var searchFocusRequest = 0

    public init() {}

    public var body: some View {
        let turns = ChatTurn.make(from: appState.chatMessages)
        let workspace = ChatWorkspaceContent(
            turns: turns,
            routeFilter: routeFilter,
            searchText: searchText
        )
        let visibleTurns = workspace.navigatorTurns

        NavigationSplitView(columnVisibility: $columnVisibility) {
            ChatSidebarView(
                turns: turns,
                visibleTurns: visibleTurns,
                selectedTurnID: $selectedTurnID,
                routeFilter: $routeFilter,
                searchText: $searchText,
                searchFocusRequest: searchFocusRequest,
                onNewChat: appState.clearChat,
                onSelectTurn: { showsInspector = true }
            )
            .navigationSplitViewColumnWidth(
                min: ChatWorkspaceChrome.sidebarMinimumWidth,
                ideal: ChatWorkspaceChrome.sidebarWidth,
                max: ChatWorkspaceChrome.sidebarMaximumWidth
            )
        } detail: {
            VStack(spacing: 0) {
                ChatToolbar(
                    title: chatTitle(in: turns),
                    showsSidebar: showsSidebar,
                    showsInspector: showsInspector,
                    onToggleSidebar: toggleSidebar,
                    onFocusSearch: focusSearch,
                    onToggleInspector: { showsInspector.toggle() },
                    canRetry: appState.canRetryChat,
                    canClear: appState.canClearChat,
                    onRetry: retryLastTurn,
                    onClear: appState.clearChat
                )
                Divider()
                ChatConversationView(
                    turns: workspace.transcriptTurns,
                    selectedTurnID: $selectedTurnID,
                    canRetry: appState.canRetryChat,
                    onRetry: retryLastTurn,
                    onOpenRouting: { turn in
                        selectedTurnID = turn.id
                        showsInspector = true
                    }
                )
                ChatComposerView(
                    draft: $appState.chatDraft,
                    destination: $appState.chatDestination,
                    destinations: appState.chatDestinations,
                    isSending: appState.isSendingMessage,
                    canSend: appState.canSendMessage,
                    onSend: {
                        followsLatestTurn = true
                        appState.sendChatDraft()
                    },
                    onStop: appState.stopChatResponse
                )
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .inspector(isPresented: $showsInspector) {
                RoutingOutputsPanel(
                    turn: selectedTurn(in: turns),
                    onClose: { showsInspector = false }
                )
                .inspectorColumnWidth(
                    min: ChatWorkspaceChrome.inspectorMinimumWidth,
                    ideal: ChatWorkspaceChrome.inspectorWidth,
                    max: ChatWorkspaceChrome.inspectorMaximumWidth
                )
            }
        }
        .navigationSplitViewStyle(.prominentDetail)
        .frame(
            minWidth: ChatWorkspaceChrome.minimumWindowWidth,
            minHeight: ChatWorkspaceChrome.minimumWindowHeight
        )
        .background(ChatWorkspaceChrome.canvas)
        .onAppear {
            selectedTurnID = selectedTurnID ?? visibleTurns.last?.id
        }
        .onChange(of: turns.map(\.id)) {
            if turns.isEmpty {
                resetWorkspace()
            } else {
                selectedTurnID = ChatWorkspaceSelectionPolicy.resolvedTurnID(
                    current: selectedTurnID,
                    followsLatest: followsLatestTurn,
                    turnIDs: turns.map(\.id)
                )
            }
        }
        .onChange(of: selectedTurnID) {
            guard let selectedTurnID else {
                followsLatestTurn = true
                return
            }
            followsLatestTurn = selectedTurnID == turns.last?.id
        }
    }

    private var showsSidebar: Bool {
        columnVisibility != .detailOnly
    }

    private func toggleSidebar() {
        columnVisibility = showsSidebar ? .detailOnly : .all
    }

    private func focusSearch() {
        columnVisibility = .all
        searchFocusRequest += 1
    }

    private func resetWorkspace() {
        selectedTurnID = nil
        routeFilter = .all
        searchText = ""
        followsLatestTurn = true
    }

    private func retryLastTurn() {
        followsLatestTurn = true
        appState.retryLastChatTurn()
    }

    private func chatTitle(in turns: [ChatTurn]) -> String {
        guard let firstPrompt = turns.first?.prompt.text,
              !firstPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return "New chat"
        }
        return firstPrompt
    }

    private func selectedTurn(in turns: [ChatTurn]) -> ChatTurn? {
        guard let selectedTurnID else {
            return nil
        }
        return turns.first { $0.id == selectedTurnID }
    }
}

private struct ChatToolbar: View {
    let title: String
    let showsSidebar: Bool
    let showsInspector: Bool
    let onToggleSidebar: () -> Void
    let onFocusSearch: () -> Void
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
            .accessibilityLabel(showsSidebar ? "Hide conversation navigator" : "Show conversation navigator")
            .help(showsSidebar ? "Hide turn navigator" : "Show turn navigator")
            .keyboardShortcut("s", modifiers: [.command, .control])

            Button(action: onFocusSearch) {
                Image(systemName: "magnifyingglass")
            }
            .accessibilityLabel("Search conversation")
            .keyboardShortcut("f", modifiers: .command)
            .help("Search conversation (Command-F)")

            Text(title)
                .font(.headline.weight(.semibold))
                .lineLimit(1)
            Spacer()
            if canRetry {
                Button(action: onRetry) {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("Retry last response")
                .keyboardShortcut("r", modifiers: .command)
                .help("Retry the last response")
            }
            Button(action: onToggleInspector) {
                Image(systemName: "sidebar.right")
                    .symbolVariant(showsInspector ? .fill : .none)
            }
            .accessibilityLabel(showsInspector ? "Hide routing inspector" : "Show routing inspector")
            .keyboardShortcut("i", modifiers: [.command, .control])
            .help(showsInspector ? "Hide routing inspector" : "Show routing inspector")
            Button(action: onClear) {
                Image(systemName: "square.and.pencil")
            }
            .disabled(!canClear)
            .accessibilityLabel("New chat")
            .keyboardShortcut("n", modifiers: .command)
            .help("Start a new in-memory chat (Command-N)")
        }
        .buttonStyle(.borderless)
        .controlSize(.small)
        .padding(.horizontal, 18)
        .frame(height: 48)
        .background(ChatWorkspaceChrome.canvas)
    }
}
