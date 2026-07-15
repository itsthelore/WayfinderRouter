import AppKit
import Combine
import SwiftUI

@MainActor
final class PopoverController {
    private let appState: AppState
    private let panel: AnchoredPopoverPanel
    private let endpointSubmenuController: EndpointStatusSubmenuController
    private let endpointSubmenuState: EndpointSubmenuState
    private var hostingController: NSViewController?
    private var overviewCancellable: AnyCancellable?
    private weak var anchorButton: NSStatusBarButton?
    private var localEventMonitor: Any?
    private var globalEventMonitor: Any?
    private var endpointOpenTask: Task<Void, Never>?
    private var endpointCloseTask: Task<Void, Never>?
    private var endpointAnchorFrame = NSRect.zero
    private var isClosing = false

    init(
        appState: AppState,
        chatAvailability: FeatureAvailability,
        onOpenChat: (() -> Void)?,
        onOpenSettings: @escaping () -> Void,
        onQuit: @escaping () -> Void
    ) {
        let panel = AnchoredPopoverPanel(
            contentRect: NSRect(
                x: 0,
                y: 0,
                width: WayfinderPopoverView.contentWidth,
                height: PopoverPanelSizing.minimumHeight
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

        let endpointSubmenuState = EndpointSubmenuState()
        self.appState = appState
        self.panel = panel
        self.endpointSubmenuController = EndpointStatusSubmenuController(
            appState: appState,
            submenuState: endpointSubmenuState
        )
        self.endpointSubmenuState = endpointSubmenuState
        endpointSubmenuController.onHoverChange = { [weak self] isHovering in
            self?.handleEndpointSubmenuHover(isHovering)
        }
        panel.onResignKey = { [weak self] in
            self?.close()
        }

        let rootView = WayfinderPopoverView(
            chatAvailability: chatAvailability,
            onOpenChat: onOpenChat.map { onOpenChat in { [weak self] in
                self?.close()
                onOpenChat()
            } },
            onEndpointAnchorFrameChange: { [weak self] frame in
                self?.updateEndpointAnchorFrame(frame)
            },
            onEndpointHoverChange: { [weak self] isHovering, frame in
                self?.handleEndpointRowHover(isHovering, anchorFrame: frame)
            },
            onOpenEndpointStatus: { [weak self] frame in
                self?.openEndpointSubmenu(anchorFrame: frame)
            },
            onCloseEndpointStatus: { [weak self] in
                self?.closeEndpointSubmenu()
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
            .environmentObject(endpointSubmenuState)

        let hostingController = EscapeHostingController(rootView: rootView)
        hostingController.onEscape = { [weak self] in
            guard let self else { return }
            if self.endpointSubmenuController.isVisible {
                self.closeEndpointSubmenu()
            } else {
                self.close()
            }
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
        hostingController.view.layer?.cornerRadius = WayfinderPopoverView.contentCornerRadius
        hostingController.view.layer?.masksToBounds = true

        self.hostingController = hostingController
        panel.contentViewController = hostingController
        overviewCancellable = appState.$gatewayOverview
            .dropFirst()
            .sink { [weak self] _ in
                Task { @MainActor [weak self] in
                    await Task.yield()
                    self?.resizeVisiblePanelToFit()
                    self?.endpointSubmenuController.refreshLayout()
                }
            }
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
        appState.refreshStats()
        resizePanelToFit(relativeTo: button, display: false)
        panel.orderFrontRegardless()
        panel.makeKey()
        installEventMonitors()
    }

    private func close() {
        guard !isClosing else { return }
        isClosing = true
        defer { isClosing = false }

        anchorButton = nil
        closeEndpointSubmenu()
        removeEventMonitors()
        panel.orderOut(nil)
    }

    private func resizeVisiblePanelToFit() {
        guard panel.isVisible, let anchorButton else {
            return
        }
        resizePanelToFit(relativeTo: anchorButton, display: true)
    }

    private func resizePanelToFit(relativeTo button: NSStatusBarButton, display: Bool) {
        panel.setFrame(
            panelFrame(relativeTo: button, size: fittedContentSize()),
            display: display
        )
    }

    private func fittedContentSize() -> NSSize {
        guard let view = hostingController?.view else {
            return NSSize(
                width: WayfinderPopoverView.contentWidth,
                height: PopoverPanelSizing.minimumHeight
            )
        }

        view.frame.size.width = WayfinderPopoverView.contentWidth
        view.needsLayout = true
        view.layoutSubtreeIfNeeded()
        return NSSize(
            width: WayfinderPopoverView.contentWidth,
            height: PopoverPanelSizing.clampedHeight(view.fittingSize.height)
        )
    }

    private func panelFrame(relativeTo button: NSStatusBarButton, size: NSSize) -> NSRect {
        let buttonFrame = screenFrame(for: button)
        let screen = button.window?.screen ?? NSScreen.main
        let visibleFrame = screen?.visibleFrame ?? buttonFrame
        let inset: CGFloat = 8
        let gap: CGFloat = 4

        let clampedX = PopoverPanelPlacement.leftAlignedX(
            anchorMinX: buttonFrame.minX,
            visibleMinX: visibleFrame.minX,
            visibleMaxX: visibleFrame.maxX,
            panelWidth: size.width,
            inset: inset
        )

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
            if self.endpointSubmenuController.contains(event) {
                self.cancelEndpointClose()
                return event
            }
            if event.window === self.panel,
               self.endpointSubmenuController.isVisible,
               let location = self.screenLocation(for: event),
               !self.endpointAnchorFrame.contains(location) {
                self.closeEndpointSubmenu()
            }
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
        if event.window === panel || endpointSubmenuController.contains(event) {
            return true
        }
        guard let anchorButton, event.window === anchorButton.window else {
            return false
        }
        let location = anchorButton.convert(event.locationInWindow, from: nil)
        return anchorButton.bounds.contains(location)
    }

    private func screenLocation(for event: NSEvent) -> NSPoint? {
        guard let window = event.window else { return nil }
        return window.convertPoint(toScreen: event.locationInWindow)
    }

    private func updateEndpointAnchorFrame(_ frame: NSRect) {
        guard !frame.isEmpty else { return }
        endpointAnchorFrame = frame
        endpointSubmenuController.updateAnchorFrame(frame)
    }

    private func handleEndpointRowHover(_ isHovering: Bool, anchorFrame: NSRect) {
        if !anchorFrame.isEmpty {
            updateEndpointAnchorFrame(anchorFrame)
        }
        if isHovering {
            cancelEndpointClose()
            scheduleEndpointOpen()
        } else {
            endpointOpenTask?.cancel()
            endpointOpenTask = nil
            scheduleEndpointClose()
        }
    }

    private func handleEndpointSubmenuHover(_ isHovering: Bool) {
        if isHovering {
            cancelEndpointClose()
        } else {
            scheduleEndpointClose()
        }
    }

    private func scheduleEndpointOpen() {
        guard !endpointSubmenuController.isVisible, !endpointAnchorFrame.isEmpty else { return }
        endpointOpenTask?.cancel()
        endpointOpenTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(150))
            guard !Task.isCancelled, let self else { return }
            self.openEndpointSubmenu(anchorFrame: self.endpointAnchorFrame)
        }
    }

    private func openEndpointSubmenu(anchorFrame: NSRect) {
        guard panel.isVisible else { return }
        endpointOpenTask?.cancel()
        endpointOpenTask = nil
        cancelEndpointClose()
        updateEndpointAnchorFrame(anchorFrame)
        endpointSubmenuController.show(anchorFrame: endpointAnchorFrame, relativeTo: panel)
        endpointSubmenuState.present(itemCount: appState.gatewayOverview.endpoints.count)
    }

    private func scheduleEndpointClose() {
        guard endpointSubmenuController.isVisible else { return }
        endpointCloseTask?.cancel()
        endpointCloseTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(250))
            guard !Task.isCancelled else { return }
            self?.closeEndpointSubmenu()
        }
    }

    private func cancelEndpointClose() {
        endpointCloseTask?.cancel()
        endpointCloseTask = nil
    }

    private func closeEndpointSubmenu() {
        endpointOpenTask?.cancel()
        endpointOpenTask = nil
        cancelEndpointClose()
        endpointSubmenuController.close()
        endpointSubmenuState.dismiss()
    }
}

private final class AnchoredPopoverPanel: NSPanel {
    var onResignKey: (() -> Void)?

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func resignKey() {
        super.resignKey()
        onResignKey?()
    }
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
