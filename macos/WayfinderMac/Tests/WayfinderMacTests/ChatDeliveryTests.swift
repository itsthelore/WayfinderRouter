import Foundation
import XCTest
@testable import WayfinderMacCore

final class ChatDeliveryTests: XCTestCase {
    func testGatewayStreamDecoderPreservesDecisionTextAndCompletionOrder() throws {
        var decoder = GatewayStreamDecoder(prompt: "Hello")
        let metadata = #"data: {"wayfinder":{"model":"apple-local","score":0.1,"mode":"scored","features":{"word_count":1},"contributions":[],"tiers":[{"min_score":0.0,"model":"apple-local"},{"min_score":0.5,"model":"cloud"}]}}"#

        let first = try decoder.consume(line: metadata)
        let second = try decoder.consume(line: #"data: {"choices":[{"delta":{"content":"Hi "}}]}"#)
        let third = try decoder.consume(line: #"data: {"choices":[{"delta":{"content":"there"}}]}"#)
        let done = try decoder.consume(line: "data: [DONE]")

        guard case let .decision(decision) = first.first else {
            return XCTFail("Expected decision metadata first")
        }
        XCTAssertEqual(decision.prompt, "Hello")
        XCTAssertEqual(decision.provider, "apple-local")
        XCTAssertEqual(decision.route, .local)
        XCTAssertEqual(second, [.text("Hi ")])
        XCTAssertEqual(third, [.text("there")])
        XCTAssertEqual(done, [.completed])
        XCTAssertTrue(decoder.sawDecision)
        XCTAssertTrue(decoder.sawCompletion)
    }

    func testGatewayStreamDecoderRejectsErrorEventsWithoutEchoingPayload() {
        var decoder = GatewayStreamDecoder(prompt: "private prompt")

        XCTAssertThrowsError(
            try decoder.consume(line: #"data: {"error":{"message":"private upstream detail","type":"upstream"}}"#)
        ) { error in
            XCTAssertEqual(error as? WayfinderClientError, .invalidChatStream)
            XCTAssertFalse(error.localizedDescription.contains("private"))
        }
    }

    func testGatewayStreamDecoderRequiresMetadataFirstAndBoundsOutput() throws {
        var missingMetadata = GatewayStreamDecoder(prompt: "Hello")
        XCTAssertThrowsError(
            try missingMetadata.consume(line: #"data: {"choices":[{"delta":{"content":"early"}}]}"#)
        )

        var bounded = GatewayStreamDecoder(prompt: "Hello", maximumResponseCharacters: 4)
        _ = try bounded.consume(line: #"data: {"wayfinder":{"model":"local","score":0.1,"mode":"scored","features":{},"contributions":[],"tiers":[{"min_score":0.0,"model":"local"}]}}"#)
        XCTAssertEqual(
            try bounded.consume(line: #"data: {"choices":[{"delta":{"content":"four"}}]}"#),
            [.text("four")]
        )
        XCTAssertThrowsError(
            try bounded.consume(line: #"data: {"choices":[{"delta":{"content":"!"}}]}"#)
        )
    }

    func testConversationBoundsKeepRecentCompleteTurnsAndRejectOversizedMessages() throws {
        let messages = (0..<24).map {
            ChatRequestMessage(role: $0.isMultiple(of: 2) ? "user" : "assistant", content: "message-\($0)")
        }

        let bounded = try GatewayWayfinderClient.boundedChatMessages(messages)

        XCTAssertEqual(bounded.count, 20)
        XCTAssertEqual(bounded.first?.content, "message-4")
        XCTAssertEqual(bounded.last?.content, "message-23")
        XCTAssertThrowsError(
            try GatewayWayfinderClient.boundedChatMessages([
                ChatRequestMessage(role: "user", content: String(repeating: "x", count: 32_769))
            ])
        ) { error in
            XCTAssertEqual(error as? WayfinderClientError, .conversationTooLarge)
        }
    }

    @MainActor
    func testAppStateBuildsARealAssistantReplyAndRetainsDecision() async throws {
        let decision = Self.decision
        let state = AppState(client: ScriptedChatClient(events: [
            .decision(decision),
            .text("Hello "),
            .text("from Wayfinder."),
            .completed,
        ]))
        state.chatDraft = "Hello"

        state.sendChatDraft()
        try await waitUntil { !state.isSendingMessage }

        XCTAssertEqual(state.chatMessages.count, 2)
        XCTAssertEqual(state.chatMessages[0].role, .user)
        XCTAssertEqual(state.chatMessages[1].role, .assistant)
        XCTAssertEqual(state.chatMessages[1].text, "Hello from Wayfinder.")
        XCTAssertEqual(state.chatMessages[1].decision, decision)
        XCTAssertEqual(state.chatMessages[1].state, .complete)
        XCTAssertEqual(AppState.chatRequestMessages(from: state.chatMessages), [
            ChatRequestMessage(role: "user", content: "Hello"),
            ChatRequestMessage(role: "assistant", content: "Hello from Wayfinder."),
        ])
    }

    @MainActor
    func testStopMarksPartialResponseAndAllowsRetry() async throws {
        let state = AppState(client: ScriptedChatClient(
            events: [.decision(Self.decision), .text("Partial")],
            delayNanoseconds: 5_000_000_000
        ))
        state.chatDraft = "Long answer"

        state.sendChatDraft()
        try await waitUntil { state.chatMessages.last?.text == "Partial" }
        state.stopChatResponse()
        try await waitUntil { !state.isSendingMessage }

        XCTAssertEqual(state.chatMessages.last?.state, .stopped)
        XCTAssertEqual(state.chatMessages.last?.text, "Partial")
        XCTAssertTrue(state.canRetryChat)
        XCTAssertEqual(AppState.chatRequestMessages(from: state.chatMessages), [
            ChatRequestMessage(role: "user", content: "Long answer")
        ])
    }

    @MainActor
    private func waitUntil(
        timeoutNanoseconds: UInt64 = 1_000_000_000,
        condition: @escaping @MainActor () -> Bool
    ) async throws {
        let started = ContinuousClock.now
        while !condition() {
            if ContinuousClock.now - started > .nanoseconds(Int64(timeoutNanoseconds)) {
                XCTFail("Timed out waiting for Chat state")
                return
            }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    fileprivate static let decision = RoutingDecision(
        prompt: "Hello",
        route: .local,
        provider: "apple-local",
        score: 0.1,
        mode: "scored",
        explanation: "A short local turn.",
        features: []
    )
}

private struct ScriptedChatClient: WayfinderClient {
    let events: [ChatStreamEvent]
    var delayNanoseconds: UInt64 = 0

    func route(prompt: String) async throws -> RoutingDecision {
        ChatDeliveryTests.decision
    }

    func streamChat(messages: [ChatRequestMessage]) -> AsyncThrowingStream<ChatStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for event in events {
                        continuation.yield(event)
                    }
                    if delayNanoseconds > 0 {
                        try await Task.sleep(nanoseconds: delayNanoseconds)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func loadStats(range: StatsRange) async throws -> RoutingStats {
        RoutingStats(
            localPercent: 1,
            cloudPercent: 0,
            totalTurns: 1,
            savedToday: 0,
            savedLast30Days: 0,
            cloudSpendToday: 0,
            percentVsAlwaysCloud: 1,
            averageRoutingTimeMilliseconds: 0,
            updatedAt: Date(),
            isRunning: true
        )
    }
}
