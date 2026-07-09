import AppKit

@MainActor
final class StatusItemController: NSObject {
    private let statusItem: NSStatusItem
    private let popoverController: PopoverController

    init(
        appState: AppState,
        onOpenChat: @escaping () -> Void,
        onOpenSettings: @escaping () -> Void,
        onQuit: @escaping () -> Void
    ) {
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        self.popoverController = PopoverController(
            appState: appState,
            onOpenChat: onOpenChat,
            onOpenSettings: onOpenSettings,
            onQuit: onQuit
        )
        super.init()
        configureButton()
    }

    private func configureButton() {
        guard let button = statusItem.button else {
            return
        }

        button.toolTip = "Wayfinder"
        if let image = NSImage(
            systemSymbolName: "arrow.triangle.branch",
            accessibilityDescription: "Wayfinder"
        ) {
            image.isTemplate = true
            button.image = image
        } else {
            button.title = "W"
        }
        button.target = self
        button.action = #selector(togglePopover)
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else {
            return
        }
        popoverController.toggle(relativeTo: button)
    }
}
