import AppKit
import SwiftUI

@MainActor
final class EndpointSubmenuState: ObservableObject {
    @Published var isPresented = false
    @Published private(set) var selectedIndex: Int?

    func present(itemCount: Int) {
        isPresented = true
        selectedIndex = itemCount > 0 ? 0 : nil
    }

    func dismiss() {
        isPresented = false
        selectedIndex = nil
    }

    func moveSelection(by offset: Int, itemCount: Int) {
        guard isPresented, itemCount > 0 else { return }
        let current = selectedIndex ?? 0
        selectedIndex = min(max(current + offset, 0), itemCount - 1)
    }
}

struct EndpointStatusRow: View {
    let presentation: EndpointStatusPresentation
    let onAnchorFrameChange: (NSRect) -> Void
    let onHoverChange: (Bool, NSRect) -> Void
    let onOpen: (NSRect) -> Void
    let onClose: () -> Void

    @EnvironmentObject private var submenuState: EndpointSubmenuState
    @State private var anchorFrame = NSRect.zero
    @State private var hovering = false
    @FocusState private var isFocused: Bool

    var body: some View {
        Button {
            onOpen(anchorFrame)
        } label: {
            HStack(spacing: NativeMenuMetrics.rowSpacing) {
                Image(systemName: "waveform.path.ecg")
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: NativeMenuMetrics.symbolSlotWidth)
                    .accessibilityHidden(true)

                Text("Endpoint Status")
                    .font(.system(size: 13, weight: .regular))

                Spacer(minLength: 8)

                Text(presentation.summary)
                    .font(.system(size: 12, weight: .regular))

                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .regular))
                    .accessibilityHidden(true)
            }
            .foregroundStyle(submenuState.isPresented ? Color.white : Color.primary)
            .frame(height: NativeMenuMetrics.rowHeight)
            .padding(.horizontal, NativeMenuMetrics.horizontalPadding)
            .contentShape(Rectangle())
            .background(rowBackground)
            .background {
                ScreenFrameReader { frame in
                    guard frame != anchorFrame else { return }
                    anchorFrame = frame
                    onAnchorFrameChange(frame)
                }
            }
        }
        .buttonStyle(.plain)
        .focused($isFocused)
        .onHover { isHovering in
            hovering = isHovering
            onHoverChange(isHovering, anchorFrame)
        }
        .onKeyPress(.rightArrow) {
            guard isFocused else { return .ignored }
            onOpen(anchorFrame)
            return .handled
        }
        .onKeyPress(.leftArrow) {
            guard isFocused, submenuState.isPresented else { return .ignored }
            onClose()
            return .handled
        }
        .onKeyPress(.downArrow) {
            guard isFocused, submenuState.isPresented else { return .ignored }
            submenuState.moveSelection(by: 1, itemCount: presentation.endpoints.count)
            return .handled
        }
        .onKeyPress(.upArrow) {
            guard isFocused, submenuState.isPresented else { return .ignored }
            submenuState.moveSelection(by: -1, itemCount: presentation.endpoints.count)
            return .handled
        }
        .accessibilityLabel(Text(presentation.accessibilityLabel))
        .accessibilityHint(Text("Opens endpoint details. Use Up and Down Arrow to review endpoints, and Left Arrow to close."))
        .accessibilityAddTraits(submenuState.isPresented ? .isSelected : [])
    }

    @ViewBuilder
    private var rowBackground: some View {
        if submenuState.isPresented {
            Rectangle().fill(Color.accentColor)
        } else if hovering {
            Rectangle().fill(Color.primary.opacity(0.06))
        } else {
            Color.clear
        }
    }
}

struct EndpointStatusSubmenuView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var submenuState: EndpointSubmenuState
    let onHoverChange: (Bool) -> Void

    private var presentation: EndpointStatusPresentation {
        EndpointStatusPresentation(overview: appState.gatewayOverview)
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    if let emptyMessage = presentation.emptyMessage {
                        EndpointStatusPlaceholderRow(message: emptyMessage)
                    } else {
                        ForEach(presentation.endpoints.indices, id: \.self) { index in
                            EndpointStatusSubmenuRow(
                                endpoint: presentation.endpoints[index],
                                isSelected: submenuState.selectedIndex == index
                            )
                            .id(presentation.endpoints[index].id)
                        }
                    }
                }
                .padding(.vertical, EndpointSubmenuSizing.verticalPadding / 2)
            }
            .scrollIndicators(presentation.endpoints.count > 9 ? .visible : .hidden)
            .onChange(of: submenuState.selectedIndex) { _, selectedIndex in
                guard let selectedIndex,
                      presentation.endpoints.indices.contains(selectedIndex) else { return }
                withAnimation(.none) {
                    proxy.scrollTo(presentation.endpoints[selectedIndex].id, anchor: .center)
                }
            }
        }
        .frame(width: EndpointSubmenuSizing.width, height: EndpointSubmenuSizing.contentHeight(rowCount: presentation.endpoints.count))
        .background {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.regularMaterial)
        }
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Color(nsColor: .separatorColor).opacity(0.42), lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .onHover(perform: onHoverChange)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Configured endpoint status")
    }
}

private struct EndpointStatusSubmenuRow: View {
    let endpoint: EndpointDisplayStatus
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(endpoint.state.tint)
                .frame(width: 8, height: 8)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 1) {
                Text(endpoint.providerName)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                    .truncationMode(.middle)

                if let detailText = endpoint.detailText {
                    Text(detailText)
                        .font(.system(size: 10.5, weight: .regular))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }

            Spacer(minLength: 12)

            Text(endpoint.state.title)
                .font(.system(size: 12, weight: .regular))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(height: EndpointSubmenuSizing.rowHeight)
        .padding(.horizontal, 14)
        .background(isSelected ? Color.accentColor.opacity(0.14) : Color.clear)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [endpoint.providerName, endpoint.modelName, "route \(endpoint.name)", endpoint.state.title]
                .compactMap { $0 }
                .joined(separator: ", ")
        )
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

private struct EndpointStatusPlaceholderRow: View {
    let message: String

    var body: some View {
        HStack {
            Text(message)
                .font(.system(size: 13, weight: .regular))
                .foregroundStyle(.secondary)
            Spacer(minLength: 0)
        }
        .frame(height: EndpointSubmenuSizing.rowHeight)
        .padding(.horizontal, 14)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(message)
    }
}

private extension EndpointState {
    var tint: Color {
        switch self {
        case .ready:
            return WayfinderTheme.local
        case .signIn, .checkKey:
            return WayfinderTheme.cloud
        case .disabled:
            return Color.secondary.opacity(0.55)
        case .unavailable:
            return WayfinderTheme.cloud.opacity(0.75)
        }
    }
}

private struct ScreenFrameReader: NSViewRepresentable {
    let onFrameChange: (NSRect) -> Void

    func makeNSView(context: Context) -> FrameReportingView {
        let view = FrameReportingView()
        view.onFrameChange = onFrameChange
        return view
    }

    func updateNSView(_ nsView: FrameReportingView, context: Context) {
        nsView.onFrameChange = onFrameChange
        nsView.reportFrame()
    }
}

private final class FrameReportingView: NSView {
    var onFrameChange: ((NSRect) -> Void)?
    private var lastFrame = NSRect.null

    override func layout() {
        super.layout()
        reportFrame()
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        reportFrame()
    }

    func reportFrame() {
        guard let window else { return }
        let screenFrame = window.convertToScreen(convert(bounds, to: nil))
        guard screenFrame != lastFrame else { return }
        lastFrame = screenFrame
        Task { @MainActor [weak self] in
            self?.onFrameChange?(screenFrame)
        }
    }
}
