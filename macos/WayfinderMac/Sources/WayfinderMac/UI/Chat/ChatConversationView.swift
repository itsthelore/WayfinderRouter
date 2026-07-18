import SwiftUI

public struct ChatConversationView: View {
    let turns: [ChatTurn]
    let hasHistory: Bool
    @Binding var selectedDecisionID: UUID?
    let onOpenDecision: (RoutingDecision) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    public init(
        turns: [ChatTurn],
        hasHistory: Bool,
        selectedDecisionID: Binding<UUID?>,
        onOpenDecision: @escaping (RoutingDecision) -> Void = { _ in }
    ) {
        self.turns = turns
        self.hasHistory = hasHistory
        self._selectedDecisionID = selectedDecisionID
        self.onOpenDecision = onOpenDecision
    }

    public var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 28) {
                    if turns.isEmpty {
                        EmptyFilteredHistory(hasHistory: hasHistory)
                            .padding(.top, 112)
                    } else {
                        ForEach(Array(turns.enumerated()), id: \.element.id) { index, turn in
                            ChatTurnHistoryRow(
                                turn: turn,
                                isLast: index == turns.count - 1,
                                selectedDecisionID: selectedDecisionID,
                                onSelectDecision: { decision in
                                    selectedDecisionID = decision.id
                                    onOpenDecision(decision)
                                }
                            )
                                .id(turn.id)
                        }
                    }
                }
                .frame(maxWidth: ChatWorkspaceChrome.conversationWidth, alignment: .leading)
                .padding(.horizontal, 36)
                .padding(.top, 34)
                .padding(.bottom, 28)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .onChange(of: scrollRevision) {
                if let last = turns.last {
                    if reduceMotion {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    } else {
                        withAnimation(.easeOut(duration: 0.2)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }
        }
        .background(ChatWorkspaceChrome.canvas)
        .textSelection(.enabled)
    }

    private var scrollRevision: Int {
        turns.reduce(turns.count) { partial, turn in
            partial + (turn.response?.text.count ?? 0)
        }
    }
}

private struct EmptyFilteredHistory: View {
    let hasHistory: Bool

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: hasHistory ? "line.3.horizontal.decrease.circle" : "point.topleft.down.curvedto.point.bottomright.up")
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(hasHistory ? ChatWorkspaceChrome.secondaryText : WayfinderTheme.local)
            Text(hasHistory ? "No matching conversations" : "Start a conversation")
                .font(.title3.weight(.semibold))
            Text(hasHistory ? "Adjust the search or route filter to show this chat's turns." : "Ask anything. Wayfinder will choose a configured model and show you the route it took.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
        }
        .frame(maxWidth: .infinity)
    }
}

public struct ChatTurn: Identifiable, Equatable {
    public let id: UUID
    public let prompt: ChatMessage
    public let response: ChatMessage?

    static func make(from messages: [ChatMessage]) -> [ChatTurn] {
        var turns: [ChatTurn] = []

        for message in messages {
            switch message.role {
            case .user:
                turns.append(ChatTurn(id: message.id, prompt: message, response: nil))
            case .assistant:
                guard let last = turns.last else {
                    continue
                }
                turns[turns.count - 1] = ChatTurn(id: last.id, prompt: last.prompt, response: message)
            }
        }

        return turns
    }
}
