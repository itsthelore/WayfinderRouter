import SwiftUI

public struct ChatConversationView: View {
    let turns: [ChatTurn]
    @Binding var selectedDecisionID: UUID?

    public init(turns: [ChatTurn], selectedDecisionID: Binding<UUID?>) {
        self.turns = turns
        self._selectedDecisionID = selectedDecisionID
    }

    public var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if turns.isEmpty {
                        EmptyFilteredHistory()
                            .padding(.top, 120)
                    } else {
                        HistoryDateDivider()

                        ForEach(Array(turns.enumerated()), id: \.element.id) { index, turn in
                            ChatTurnHistoryRow(
                                turn: turn,
                                isLast: index == turns.count - 1,
                                selectedDecisionID: selectedDecisionID,
                                onSelectDecision: { selectedDecisionID = $0.id }
                            )
                                .id(turn.id)
                        }
                    }
                }
                .frame(maxWidth: 820, alignment: .leading)
                .padding(.horizontal, 28)
                .padding(.vertical, 22)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .onChange(of: turns.count) { _ in
                if let last = turns.last {
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
        .background(ChatWorkspaceChrome.canvas)
    }
}

private struct HistoryDateDivider: View {
    var body: some View {
        HStack(spacing: 10) {
            Text("Today")
                .font(.caption.weight(.semibold))
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            Rectangle()
                .fill(ChatWorkspaceChrome.border)
                .frame(height: 1)
        }
        .padding(.leading, 42)
        .padding(.bottom, 4)
    }
}

private struct EmptyFilteredHistory: View {
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "line.3.horizontal.decrease.circle")
                .font(.title2)
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            Text("No matching routes")
                .font(.headline)
            Text("Adjust the search or route filter to show history.")
                .font(.callout)
                .foregroundStyle(.secondary)
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
            case .router:
                guard let last = turns.last else {
                    continue
                }
                turns[turns.count - 1] = ChatTurn(id: last.id, prompt: last.prompt, response: message)
            }
        }

        return turns
    }
}
