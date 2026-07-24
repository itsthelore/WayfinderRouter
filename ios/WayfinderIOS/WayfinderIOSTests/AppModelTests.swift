import XCTest

@testable import WayfinderIOS

@MainActor
final class AppModelTests: XCTestCase {
  func testSimplePromptRoutesToOnDeviceCandidate() {
    let model = AppModel()
    model.draft = "Hello"

    model.previewRoute()

    guard case .routed(let preview) = model.routePreviewState else {
      return XCTFail("Expected a routed preview")
    }
    XCTAssertEqual(preview.destinationID, "device-preview")
    XCTAssertEqual(preview.executionSummary, "On this device")
    XCTAssertEqual(preview.score, 0.0)
    XCTAssertEqual(model.submittedPrompt, "Hello")
  }

  func testStructuredPromptRoutesToHostedCandidateWhenAllowed() {
    let model = AppModel()
    model.draft = "# Plan\n\n## Steps\n- one\n- two\n- three\n1. first\n2. second"

    model.previewRoute()

    guard case .routed(let preview) = model.routePreviewState else {
      return XCTFail("Expected a routed preview")
    }
    XCTAssertEqual(preview.destinationID, "hosted-preview")
    XCTAssertEqual(preview.executionSummary, "Hosted cloud")
    XCTAssertEqual(preview.score, 0.15)
  }

  func testOnDeviceOnlyExcludesHostedRecommendation() {
    let model = AppModel()
    model.privacyPosture = .onDeviceOnly
    model.draft = "# Plan\n\n## Steps\n- one\n- two\n- three\n1. first\n2. second"

    model.previewRoute()

    guard case .unavailable(let message) = model.routePreviewState else {
      return XCTFail("Expected no eligible route")
    }
    XCTAssertTrue(message.contains("On-Device Only"))
  }

  func testEmptyPromptDoesNotRoute() {
    let model = AppModel()
    model.draft = " \n "

    model.previewRoute()

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

  func testNewChatClearsTransientConversationState() {
    let model = AppModel()
    model.selectedTab = .settings
    model.draft = "Hello"
    model.previewRoute()

    model.startNewChat()

    XCTAssertEqual(model.selectedTab, .chat)
    XCTAssertEqual(model.draft, "")
    XCTAssertNil(model.submittedPrompt)
    XCTAssertEqual(model.routePreviewState, .idle)
  }
}
