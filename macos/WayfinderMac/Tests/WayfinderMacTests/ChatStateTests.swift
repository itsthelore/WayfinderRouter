import XCTest
@testable import WayfinderMacCore

@MainActor
final class ChatStateTests: XCTestCase {
    func testAppStateStartsWithAnEmptyChatSession() {
        let state = AppState(client: MockWayfinderClient())

        XCTAssertTrue(state.chatMessages.isEmpty)
        XCTAssertTrue(state.chatDraft.isEmpty)
        XCTAssertFalse(state.isSendingMessage)
        XCTAssertEqual(state.gatewayOverview.gateway.title, "Checking")
        XCTAssertEqual(state.gatewayOverview.hosted.title, "Checking")
        XCTAssertEqual(state.routingStats.totalTurns, 0)
    }

    func testFailedResponseRemainsDistinctFromPendingTurn() {
        let prompt = ChatMessage(role: .user, text: "Route this")
        let failure = ChatMessage(role: .assistant, text: "Gateway unavailable", state: .failed)

        let pending = ChatTurn.make(from: [prompt])
        let failed = ChatTurn.make(from: [prompt, failure])

        XCTAssertNil(pending.first?.response)
        XCTAssertEqual(failed.first?.response, failure)
        XCTAssertNil(failed.first?.response?.decision)
    }
}
