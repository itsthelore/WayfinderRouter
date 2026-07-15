import AppKit
import SwiftUI

@MainActor
final class EndpointStatusSubmenuController {
    private let appState: AppState
    private let panel: EndpointSubmenuPanel
    private let hoverRelay = EndpointSubmenuHoverRelay()
    private var hostingController: NSViewController?
    private weak var parentPanel: NSPanel?
    private(set) var anchorFrame = NSRect.zero

    var onHoverChange: ((Bool) -> Void)? {
        get { hoverRelay.handler }
        set { hoverRelay.handler = newValue }
    }

    var isVisible: Bool { panel.isVisible }

    init(appState: AppState, submenuState: EndpointSubmenuState) {
        self.appState = appState

        let panel = EndpointSubmenuPanel(
            contentRect: NSRect(
                origin: .zero,
                size: NSSize(
                    width: EndpointSubmenuSizing.width,
                    height: EndpointSubmenuSizing.contentHeight(rowCount: 0)
                )
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

        let relay = hoverRelay
        let rootView = EndpointStatusSubmenuView { isHovering in
            relay.handler?(isHovering)
        }
        .environmentObject(appState)
        .environmentObject(submenuState)
        let hostingController = NSHostingController(rootView: rootView)
        hostingController.view.wantsLayer = true
        hostingController.view.layer?.cornerRadius = 12
        hostingController.view.layer?.masksToBounds = true
        self.hostingController = hostingController
        panel.contentViewController = hostingController
    }

    func show(anchorFrame: NSRect, relativeTo parentPanel: NSPanel) {
        guard !anchorFrame.isEmpty else { return }
        self.anchorFrame = anchorFrame
        self.parentPanel = parentPanel
        reposition()
        if panel.parent == nil {
            parentPanel.addChildWindow(panel, ordered: .above)
        }
        panel.orderFront(nil)
        NSAccessibility.post(
            element: hostingController?.view as Any,
            notification: .layoutChanged
        )
    }

    func updateAnchorFrame(_ anchorFrame: NSRect) {
        guard !anchorFrame.isEmpty else { return }
        self.anchorFrame = anchorFrame
        if isVisible {
            reposition()
        }
    }

    func refreshLayout() {
        guard isVisible else { return }
        hostingController?.view.needsLayout = true
        hostingController?.view.layoutSubtreeIfNeeded()
        reposition()
    }

    func close() {
        guard isVisible || panel.parent != nil else { return }
        if let parentPanel, panel.parent === parentPanel {
            parentPanel.removeChildWindow(panel)
        }
        panel.orderOut(nil)
        parentPanel = nil
    }

    func contains(_ event: NSEvent) -> Bool {
        event.window === panel
    }

    private func reposition() {
        guard let parentPanel else { return }
        let rowCount = appState.gatewayOverview.endpoints.count
        let size = NSSize(
            width: EndpointSubmenuSizing.width,
            height: EndpointSubmenuSizing.contentHeight(rowCount: rowCount)
        )
        let visibleFrame = parentPanel.screen?.visibleFrame
            ?? NSScreen.main?.visibleFrame
            ?? parentPanel.frame
        let frame = EndpointSubmenuPlacement.frame(
            anchorFrame: anchorFrame,
            parentFrame: parentPanel.frame,
            visibleFrame: visibleFrame,
            size: size
        )
        panel.setFrame(frame, display: true)
    }
}

private final class EndpointSubmenuPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

private final class EndpointSubmenuHoverRelay {
    var handler: ((Bool) -> Void)?
}
