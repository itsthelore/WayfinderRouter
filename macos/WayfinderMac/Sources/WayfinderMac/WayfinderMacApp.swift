import AppKit

@MainActor
public final class AppDelegate: NSObject, NSApplicationDelegate {
    private let appState: AppState
    private let featurePolicy: ReleaseFeaturePolicy
    private let openChatOnLaunch: Bool
    private var statusItemController: StatusItemController?
    private var chatFeatureCoordinator: ChatFeatureCoordinator?
    private var settingsWindowController: SettingsWindowController?
    private var setupWindowController: SetupWindowController?
    private var setupObserver: NSObjectProtocol?
    private var settingsObserver: NSObjectProtocol?

    public init(
        client: any WayfinderClient,
        featurePolicy: ReleaseFeaturePolicy = .current,
        openChatOnLaunch: Bool = false
    ) {
        self.appState = AppState(client: client)
        self.featurePolicy = featurePolicy
        self.openChatOnLaunch = openChatOnLaunch
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
        let setupWindowController = SetupWindowController()
        self.setupWindowController = setupWindowController
        setupObserver = NotificationCenter.default.addObserver(
            forName: .wayfinderRunSetupAssistant, object: nil, queue: .main
        ) { [weak setupWindowController] _ in
            Task { @MainActor in setupWindowController?.reassessAndShow() }
        }
        settingsObserver = NotificationCenter.default.addObserver(
            forName: .wayfinderOpenSettings, object: nil, queue: .main
        ) { [weak self] notification in
            let section = SettingsWindowNavigation.section(from: notification)
            Task { @MainActor in self?.showSettingsWindow(section: section) }
        }
        NotificationCenter.default.addObserver(
            forName: .wayfinderSetupDidChange, object: nil, queue: .main
        ) { [weak appState] _ in
            Task { @MainActor in appState?.refreshSetupAssessment(); appState?.refreshStats() }
        }
        statusItemController = StatusItemController(
            appState: appState,
            chatAvailability: chatFeatureCoordinator.availability,
            onOpenChat: chatFeatureCoordinator.openAction,
            onOpenSettings: { [weak self] in self?.showSettingsWindow() },
            onQuit: { NSApp.terminate(nil) }
        )
        appState.refreshSetupAssessment()
        appState.refreshStats()
        setupWindowController.assessAndShowIfNeeded()
        if openChatOnLaunch {
            chatFeatureCoordinator.openAction?()
        }
    }

    public func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    private func showSettingsWindow(section: SettingsSection = .gateway) {
        settingsWindowController?.show(section: section)
    }
}
