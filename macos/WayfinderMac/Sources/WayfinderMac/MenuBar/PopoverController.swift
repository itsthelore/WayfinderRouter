import AppKit
import SwiftUI

@MainActor
final class PopoverController {
    private let panel: AnchoredPopoverPanel
    private weak var anchorButton: NSStatusBarButton?
    private var localEventMonitor: Any?
    private var globalEventMonitor: Any?

    init(
        appState: AppState,
        onOpenChat: @escaping () -> Void,
        onOpenSettings: @escaping () -> Void,
        onQuit: @escaping () -> Void
    ) {
        let panel = AnchoredPopoverPanel(
            contentRect: NSRect(
                x: 0,
                y: 0,
                width: WayfinderPopoverView.contentWidth,
                height: WayfinderPopoverView.contentHeight
            ),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isReleasedWhenClosed = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.level = .popUpMenu
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]

        self.panel = panel

        let rootView = WayfinderPopoverView(
            onOpenChat: { [weak self] in
                self?.close()
                onOpenChat()
            },
            onOpenSettings: { [weak self] in
                self?.close()
                onOpenSettings()
            },
            onQuit: { [weak self] in
                self?.close()
                onQuit()
            }
        )
            .environmentObject(appState)

        let hostingController = EscapeHostingController(rootView: rootView)
        hostingController.onEscape = { [weak self] in
            self?.close()
        }
        hostingController.view.wantsLayer = true
        hostingController.onCommandR = { appState.refreshStats() }
        hostingController.onCommandComma = { [weak self] in
            self?.close()
            onOpenSettings()
        }
        hostingController.onCommandQ = { [weak self] in
            self?.close()
            onQuit()
        }
        hostingController.view.layer?.cornerRadius = 20
        hostingController.view.layer?.masksToBounds = true

        panel.contentViewController = hostingController
    }

    func toggle(relativeTo button: NSStatusBarButton) {
        if panel.isVisible {
            close()
        } else {
            show(relativeTo: button)
        }
    }

    private func show(relativeTo button: NSStatusBarButton) {
        anchorButton = button
        panel.setFrame(panelFrame(relativeTo: button), display: false)
        panel.orderFrontRegardless()
        panel.makeKey()
        installEventMonitors()
    }

    private func close() {
        panel.orderOut(nil)
        anchorButton = nil
        removeEventMonitors()
    }

    private func panelFrame(relativeTo button: NSStatusBarButton) -> NSRect {
        let size = NSSize(
            width: WayfinderPopoverView.contentWidth,
            height: WayfinderPopoverView.contentHeight
        )
        let buttonFrame = screenFrame(for: button)
        let screen = button.window?.screen ?? NSScreen.main
        let visibleFrame = screen?.visibleFrame ?? buttonFrame
        let inset: CGFloat = 8
        let gap: CGFloat = 4

        let proposedX = buttonFrame.midX - (size.width / 2)
        let minX = visibleFrame.minX + inset
        let maxX = visibleFrame.maxX - size.width - inset
        let clampedX = maxX < minX ? minX : min(max(proposedX, minX), maxX)

        let proposedY = buttonFrame.minY - size.height - gap
        let minY = visibleFrame.minY + inset
        let maxY = visibleFrame.maxY - size.height - gap
        let clampedY = maxY < minY ? minY : min(max(proposedY, minY), maxY)

        return NSRect(x: clampedX, y: clampedY, width: size.width, height: size.height)
    }

    private func screenFrame(for button: NSStatusBarButton) -> NSRect {
        guard let window = button.window else {
            return NSScreen.main?.visibleFrame ?? .zero
        }
        let frameInWindow = button.convert(button.bounds, to: nil)
        return window.convertToScreen(frameInWindow)
    }

    private func installEventMonitors() {
        removeEventMonitors()

        localEventMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] event in
            guard let self else { return event }
            if !self.eventIsInsidePanel(event) {
                self.close()
            }
            return event
        }

        globalEventMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in
            Task { @MainActor in
                self?.close()
            }
        }
    }

    private func removeEventMonitors() {
        if let localEventMonitor {
            NSEvent.removeMonitor(localEventMonitor)
            self.localEventMonitor = nil
        }
        if let globalEventMonitor {
            NSEvent.removeMonitor(globalEventMonitor)
            self.globalEventMonitor = nil
        }
    }

    private func eventIsInsidePanel(_ event: NSEvent) -> Bool {
        if event.window === panel {
            return true
        }
        guard let anchorButton, event.window === anchorButton.window else {
            return false
        }
        let location = anchorButton.convert(event.locationInWindow, from: nil)
        return anchorButton.bounds.contains(location)
    }
}

private final class AnchoredPopoverPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

private final class EscapeHostingController<Content: View>: NSHostingController<Content> {
    var onEscape: (() -> Void)?
    var onCommandR: (() -> Void)?
    var onCommandComma: (() -> Void)?
    var onCommandQ: (() -> Void)?

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 {
            onEscape?()
            return
        }
        if event.modifierFlags.intersection(.deviceIndependentFlagsMask).contains(.command) {
            switch event.charactersIgnoringModifiers?.lowercased() {
            case "r":
                onCommandR?()
                return
            case ",":
                onCommandComma?()
                return
            case "q":
                onCommandQ?()
                return
            default:
                break
            }
        }
        super.keyDown(with: event)
    }
}
