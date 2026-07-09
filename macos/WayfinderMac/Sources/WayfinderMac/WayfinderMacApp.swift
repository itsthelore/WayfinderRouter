import AppKit

@MainActor
public final class AppDelegate: NSObject, NSApplicationDelegate {
    private let appState: AppState
    private var statusItemController: StatusItemController?
    private var chatWindowController: ChatWindowController?
    private var settingsWindowController: SettingsWindowController?

    public init(client: any WayfinderClient) {
        self.appState = AppState(client: client)
        super.init()
    }

    public func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        chatWindowController = ChatWindowController(appState: appState)
        settingsWindowController = SettingsWindowController(appState: appState)
        statusItemController = StatusItemController(
            appState: appState,
            onOpenChat: { [weak self] in self?.showChatWindow() },
            onOpenSettings: { [weak self] in self?.showSettingsWindow() },
            onQuit: { NSApp.terminate(nil) }
        )
    }

    public func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    private func showChatWindow() {
        chatWindowController?.show()
    }

    private func showSettingsWindow() {
        settingsWindowController?.show()
    }
}
