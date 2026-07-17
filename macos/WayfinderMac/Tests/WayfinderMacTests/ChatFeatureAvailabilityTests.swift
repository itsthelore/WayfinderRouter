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

    func testDesktopV010PolicyShipsChat() {
        XCTAssertEqual(ReleaseFeaturePolicy.current, .desktopV0_1_0)
        XCTAssertEqual(
            ReleaseFeaturePolicy.desktopV0_1_0[.chat],
            .available
        )
    }

    func testBlockedChatDoesNotInstantiateControllerOrExposeOpenAction() {
        let state = AppState(client: MockWayfinderClient())
        var creationCount = 0

        let coordinator = ChatFeatureCoordinator(
            policy: ReleaseFeaturePolicy(availability: [.chat: .blocked(reason: "Disabled for test.")]),
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
        let row = ChatPopoverRowModel(availability: .blocked(reason: "Disabled for test."))

        XCTAssertFalse(row.isEnabled)
        XCTAssertEqual(row.trailingText, "Coming later")
        XCTAssertFalse(row.showsChevron)
        XCTAssertEqual(row.accessibilityLabel, "Chat, Coming later")
        XCTAssertEqual(row.accessibilityHint, "Disabled for test.")
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

    func testDesktopPopoverRowOpensChat() {
        let row = ChatPopoverRowModel(availability: ReleaseFeaturePolicy.current[.chat])

        XCTAssertTrue(row.isEnabled)
        XCTAssertNil(row.trailingText)
        XCTAssertTrue(row.showsChevron)
        XCTAssertEqual(row.accessibilityLabel, "Chat")
        XCTAssertEqual(row.accessibilityHint, "Opens the Chat window.")
    }
}
