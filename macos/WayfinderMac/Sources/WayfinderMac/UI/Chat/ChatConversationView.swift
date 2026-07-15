import SwiftUI

public struct ChatConversationView: View {
    let turns: [ChatTurn]
    let hasHistory: Bool
    @Binding var selectedDecisionID: UUID?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    public init(turns: [ChatTurn], hasHistory: Bool, selectedDecisionID: Binding<UUID?>) {
        self.turns = turns
        self.hasHistory = hasHistory
        self._selectedDecisionID = selectedDecisionID
    }

    public var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if turns.isEmpty {
                        EmptyFilteredHistory(hasHistory: hasHistory)
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
            .onChange(of: turns.count) {
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
    let hasHistory: Bool

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "line.3.horizontal.decrease.circle")
                .font(.title2)
                .foregroundStyle(ChatWorkspaceChrome.secondaryText)
            Text(hasHistory ? "No matching routes" : "No routed turns yet")
                .font(.headline)
            Text(hasHistory ? "Adjust the search or route filter to show history." : "Route a prompt below to inspect the decision.")
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
