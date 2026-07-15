import XCTest
@testable import WayfinderMacCore

@MainActor
final class ChatFeatureAvailabilityTests: XCTestCase {
    private final class ChatWindowSpy: ChatWindowPresenting {
        private(set) var showCount = 0

        func show() {
            showCount += 1
        }
    }

    func testV1PolicyBlocksChatWithReleaseReason() {
        XCTAssertEqual(ReleaseFeaturePolicy.current, .v1)
        XCTAssertEqual(
            ReleaseFeaturePolicy.v1[.chat],
            .blocked(reason: "Chat is unavailable in this release.")
        )
    }

    func testBlockedChatDoesNotInstantiateControllerOrExposeOpenAction() {
        let state = AppState(client: MockWayfinderClient())
        var creationCount = 0

        let coordinator = ChatFeatureCoordinator(
            policy: .v1,
            appState: state,
            makeController: { _ in
                creationCount += 1
                return ChatWindowSpy()
            }
        )

        XCTAssertEqual(creationCount, 0)
        XCTAssertNil(coordinator.openAction)
    }

    func testBlockedPopoverRowIsDisabledAndHasNoChevron() {
        let row = ChatPopoverRowModel(availability: ReleaseFeaturePolicy.v1[.chat])

        XCTAssertFalse(row.isEnabled)
        XCTAssertEqual(row.trailingText, "Coming later")
        XCTAssertFalse(row.showsChevron)
        XCTAssertEqual(row.accessibilityLabel, "Chat, Coming later")
        XCTAssertEqual(row.accessibilityHint, "Chat is unavailable in this release.")
    }

    func testAvailablePolicyCreatesAndCanShowController() {
        let state = AppState(client: MockWayfinderClient())
        let spy = ChatWindowSpy()
        var creationCount = 0
        let policy = ReleaseFeaturePolicy(availability: [.chat: .available])

        let coordinator = ChatFeatureCoordinator(
            policy: policy,
            appState: state,
            makeController: { _ in
                creationCount += 1
                return spy
            }
        )

        XCTAssertEqual(creationCount, 1)
        coordinator.openAction?()
        XCTAssertEqual(spy.showCount, 1)
    }
}
