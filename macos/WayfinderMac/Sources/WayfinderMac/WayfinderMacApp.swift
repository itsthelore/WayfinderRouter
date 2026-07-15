import AppKit

@MainActor
public final class AppDelegate: NSObject, NSApplicationDelegate {
    private let appState: AppState
    private let featurePolicy: ReleaseFeaturePolicy
    private var statusItemController: StatusItemController?
    private var chatFeatureCoordinator: ChatFeatureCoordinator?
    private var settingsWindowController: SettingsWindowController?

    public init(
        client: any WayfinderClient,
        featurePolicy: ReleaseFeaturePolicy = .current
    ) {
        self.appState = AppState(client: client)
        self.featurePolicy = featurePolicy
        super.init()
    }

    public func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let chatFeatureCoordinator = ChatFeatureCoordinator(
            policy: featurePolicy,
            appState: appState
        )
        self.chatFeatureCoordinator = chatFeatureCoordinator
        settingsWindowController = SettingsWindowController(appState: appState)
        statusItemController = StatusItemController(
            appState: appState,
            chatAvailability: chatFeatureCoordinator.availability,
            onOpenChat: chatFeatureCoordinator.openAction,
            onOpenSettings: { [weak self] in self?.showSettingsWindow() },
            onQuit: { NSApp.terminate(nil) }
        )
    }

    public func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    private func showSettingsWindow() {
        settingsWindowController?.show()
    }
}
