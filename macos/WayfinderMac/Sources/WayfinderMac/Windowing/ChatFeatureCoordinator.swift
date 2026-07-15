@MainActor
protocol ChatWindowPresenting: AnyObject {
    func show()
}

@MainActor
final class ChatFeatureCoordinator {
    typealias ControllerFactory = @MainActor (AppState) -> any ChatWindowPresenting

    let availability: FeatureAvailability
    private let controller: (any ChatWindowPresenting)?

    init(
        policy: ReleaseFeaturePolicy,
        appState: AppState,
        makeController: ControllerFactory = { ChatWindowController(appState: $0) }
    ) {
        let availability = policy[.chat]
        self.availability = availability
        self.controller = availability.isAvailable ? makeController(appState) : nil
    }

    var openAction: (() -> Void)? {
        guard let controller else {
            return nil
        }
        return {
            controller.show()
        }
    }
}
