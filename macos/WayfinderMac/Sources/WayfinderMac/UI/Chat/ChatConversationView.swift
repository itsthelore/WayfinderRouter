import SwiftUI

public struct ChatConversationView: View {
    let turns: [ChatTurn]
    @Binding var selectedTurnID: UUID?
    let canRetry: Bool
    let onRetry: () -> Void
    let onOpenRouting: (ChatTurn) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isNearBottom = true

    public init(
        turns: [ChatTurn],
        selectedTurnID: Binding<UUID?>,
        canRetry: Bool = false,
        onRetry: @escaping () -> Void = {},
        onOpenRouting: @escaping (ChatTurn) -> Void = { _ in }
    ) {
        self.turns = turns
        self._selectedTurnID = selectedTurnID
        self.canRetry = canRetry
        self.onRetry = onRetry
        self.onOpenRouting = onOpenRouting
    }

    public var body: some View {
        GeometryReader { viewport in
            ScrollViewReader { proxy in
                ZStack(alignment: .bottomTrailing) {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 28) {
                            if turns.isEmpty {
                                EmptyConversation()
                                    .padding(.top, 112)
                            } else {
                                ForEach(Array(turns.enumerated()), id: \.element.id) { index, turn in
                                    ChatTurnHistoryRow(
                                        turn: turn,
                                        isLast: index == turns.count - 1,
                                        isSelected: selectedTurnID == turn.id,
                                        canRetry: canRetry && index == turns.count - 1,
                                        onRetry: onRetry,
                                        onShowRouting: {
                                            selectedTurnID = turn.id
                                            onOpenRouting(turn)
                                        }
                                    )
                                    .id(turn.id)
                                }
                            }

                            Color.clear
                                .frame(height: 1)
                                .id(Self.bottomAnchorID)
                                .background {
                                    GeometryReader { proxy in
                                        Color.clear.preference(
                                            key: ConversationBottomPreferenceKey.self,
                                            value: proxy.frame(in: .named(Self.scrollCoordinateSpace)).maxY
                                        )
                                    }
                                }
                        }
                        .frame(maxWidth: ChatWorkspaceChrome.conversationWidth, alignment: .leading)
                        .padding(.horizontal, 36)
                        .padding(.top, 34)
                        .padding(.bottom, 28)
                        .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .coordinateSpace(name: Self.scrollCoordinateSpace)
                    .onPreferenceChange(ConversationBottomPreferenceKey.self) { bottomY in
                        isNearBottom = bottomY <= viewport.size.height + 72
                    }

                    if !isNearBottom, !turns.isEmpty {
                        Button {
                            selectedTurnID = turns.last?.id
                            scrollToLatest(using: proxy)
                        } label: {
                            Label("Jump to latest", systemImage: "arrow.down")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .padding(16)
                        .help("Return to the latest response")
                    }
                }
                .onChange(of: turns.count) {
                    scrollToLatest(using: proxy)
                }
                .onChange(of: selectedTurnID) {
                    guard let selectedTurnID else { return }
                    if selectedTurnID == turns.last?.id {
                        isNearBottom = true
                        scrollToLatest(using: proxy)
                    } else {
                        isNearBottom = false
                        scroll(to: selectedTurnID, anchor: .center, using: proxy)
                    }
                }
                .onChange(of: scrollRevision) {
                    guard ChatScrollFollowPolicy.shouldFollowLatest(
                        isNearBottom: isNearBottom,
                        selectedTurnID: selectedTurnID,
                        latestTurnID: turns.last?.id
                    ) else { return }
                    scrollToLatest(using: proxy)
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

    private func scrollToLatest(using proxy: ScrollViewProxy) {
        guard !turns.isEmpty else { return }
        isNearBottom = true
        scroll(to: Self.bottomAnchorID, anchor: .bottom, using: proxy)
    }

    private func scroll<ID: Hashable>(to id: ID, anchor: UnitPoint, using proxy: ScrollViewProxy) {
        if reduceMotion {
            proxy.scrollTo(id, anchor: anchor)
        } else {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(id, anchor: anchor)
            }
        }
    }

    private static let bottomAnchorID = "wayfinder-chat-bottom"
    private static let scrollCoordinateSpace = "wayfinder-chat-scroll"
}

private struct ConversationBottomPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct EmptyConversation: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(WayfinderTheme.local)
            Text("Start a conversation")
                .font(.title3.weight(.semibold))
            Text("Ask anything. Wayfinder will choose a configured model and show you the route it took.")
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

enum ChatRoutingInspectionState: Equatable {
    case waiting
    case routed(RoutingDecision)
    case failed(String, RoutingDecision?)
    case stopped(RoutingDecision?)
    case unavailable
}

extension ChatTurn {
    var routingInspectionState: ChatRoutingInspectionState {
        guard let response else {
            return .waiting
        }

        switch response.state {
        case .streaming:
            if let decision = response.decision {
                return .routed(decision)
            }
            return .waiting
        case .failed:
            return .failed(response.text, response.decision)
        case .stopped:
            return .stopped(response.decision)
        case .complete:
            if let decision = response.decision {
                return .routed(decision)
            }
            return .unavailable
        }
    }
}
