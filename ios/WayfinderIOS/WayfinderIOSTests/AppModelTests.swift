import XCTest

@testable import WayfinderIOS

@MainActor
final class AppModelTests: XCTestCase {
  func testSimplePromptRoutesToOnDeviceCandidate() async {
    let model = AppModel()
    model.draft = "Hello"

    await model.previewRoute()

    guard case .routed(let preview) = model.routePreviewState else {
      return XCTFail("Expected a routed preview")
    }
    XCTAssertEqual(preview.destinationID, "device-preview")
    XCTAssertEqual(preview.executionSummary, "On this device")
    XCTAssertEqual(preview.score, 0.0)
    XCTAssertEqual(model.submittedPrompt, "Hello")
  }

  func testStructuredPromptRoutesToHostedCandidateWhenAllowed() async {
    let model = AppModel()
    model.draft = "# Plan\n\n## Steps\n- one\n- two\n- three\n1. first\n2. second"

    await model.previewRoute()

    guard case .routed(let preview) = model.routePreviewState else {
      return XCTFail("Expected a routed preview")
    }
    XCTAssertEqual(preview.destinationID, "hosted-preview")
    XCTAssertEqual(preview.executionSummary, "Hosted cloud")
    XCTAssertEqual(preview.score, 0.15)
  }

  func testOnDeviceOnlyExcludesHostedRecommendation() async {
    let model = AppModel()
    model.privacyPosture = .onDeviceOnly
    model.draft = "# Plan\n\n## Steps\n- one\n- two\n- three\n1. first\n2. second"

    await model.previewRoute()

    guard case .unavailable(let message) = model.routePreviewState else {
      return XCTFail("Expected no eligible route")
    }
    XCTAssertTrue(message.contains("On-Device Only"))
  }

  func testEmptyPromptDoesNotRoute() async {
    let model = AppModel()
    model.draft = " \n "

    await model.previewRoute()

    XCTAssertEqual(
      model.routePreviewState,
      .unavailable("Enter a message to preview its route.")
    )
  }

  func testRootTabsRemainCompleteAndStable() {
    XCTAssertEqual(
      AppTab.allCases.map(\.title),
      ["Chat", "Threads", "Destinations", "Settings"]
    )
  }

  func testNewChatClearsTransientConversationState() async {
    let model = AppModel()
    model.selectedTab = .settings
    model.draft = "Hello"
    await model.previewRoute()

    await model.startNewChat()

    XCTAssertEqual(model.selectedTab, .chat)
    XCTAssertEqual(model.draft, "")
    XCTAssertNil(model.submittedPrompt)
    XCTAssertEqual(model.routePreviewState, .idle)
  }

  func testSavedConversationRestoresIntoNewModel() async {
    let store = InMemoryConversationStore()
    let timestamp = Date(timeIntervalSince1970: 1_700_000_000)
    let firstModel = AppModel(
      conversationStore: store,
      now: { timestamp }
    )
    firstModel.draft = "Restore this conversation"

    await firstModel.previewRoute()

    let restoredModel = AppModel(conversationStore: store)
    await restoredModel.restoreConversations()

    XCTAssertEqual(restoredModel.threads.count, 1)
    XCTAssertEqual(
      restoredModel.activeThread?.messages.first?.content,
      "Restore this conversation"
    )
    XCTAssertEqual(restoredModel.submittedPrompt, "Restore this conversation")
  }

  func testSecondTurnAppendsToActiveConversation() async {
    let store = InMemoryConversationStore()
    let model = AppModel(conversationStore: store)
    model.draft = "First turn"
    await model.previewRoute()
    model.draft = "Second turn"

    await model.previewRoute()

    XCTAssertEqual(model.threads.count, 1)
    XCTAssertEqual(
      model.activeThread?.messages.map(\.content),
      ["First turn", "Second turn"]
    )
  }

  func testNewChatDraftRestoresWithoutCreatingThread() async {
    let store = InMemoryConversationStore()
    let firstModel = AppModel(conversationStore: store)
    firstModel.draft = "Unsent draft"
    await firstModel.saveDraft()

    let restoredModel = AppModel(conversationStore: store)
    await restoredModel.restoreConversations()

    XCTAssertEqual(restoredModel.draft, "Unsent draft")
    XCTAssertTrue(restoredModel.threads.isEmpty)
    XCTAssertNil(restoredModel.activeThreadID)
  }

  func testRetentionPolicyPrunesOldConversationOnRestore() async {
    let store = InMemoryConversationStore()
    let now = Date(timeIntervalSince1970: 10_000_000)
    let oldThread = ConversationThreadSnapshot(
      id: UUID(),
      title: "Old",
      createdAt: now.addingTimeInterval(-100 * 86_400),
      updatedAt: now.addingTimeInterval(-100 * 86_400),
      messages: [],
      draft: ""
    )
    await store.save(thread: oldThread)
    await store.save(
      workspace: ConversationWorkspaceSnapshot(
        activeThreadID: nil,
        draft: "",
        retentionDays: 30,
        updatedAt: now
      )
    )
    let model = AppModel(
      conversationStore: store,
      now: { now }
    )

    await model.restoreConversations()

    XCTAssertEqual(model.retentionPolicy, .thirtyDays)
    XCTAssertTrue(model.threads.isEmpty)
  }
}
