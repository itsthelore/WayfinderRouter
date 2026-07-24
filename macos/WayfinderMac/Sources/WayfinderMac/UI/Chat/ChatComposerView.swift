import SwiftUI

public struct ChatComposerView: View {
    @Binding var draft: String
    @Binding var destination: ChatDestination
    let destinations: [ChatDestination]
    let messageOverride: ChatDestination?
    let isSending: Bool
    let canSend: Bool
    let onSelectMessageOverride: (ChatDestination) -> Void
    let onClearMessageOverride: () -> Void
    let onSend: () -> Void
    let onStop: () -> Void

    @FocusState private var focused: Bool
    @State private var selectedSuggestionID: String?
    @State private var suggestionsDismissed = false

    public init(
        draft: Binding<String>,
        destination: Binding<ChatDestination>,
        destinations: [ChatDestination],
        messageOverride: ChatDestination?,
        isSending: Bool,
        canSend: Bool,
        onSelectMessageOverride: @escaping (ChatDestination) -> Void,
        onClearMessageOverride: @escaping () -> Void,
        onSend: @escaping () -> Void,
        onStop: @escaping () -> Void
    ) {
        self._draft = draft
        self._destination = destination
        self.destinations = destinations
        self.messageOverride = messageOverride
        self.isSending = isSending
        self.canSend = canSend
        self.onSelectMessageOverride = onSelectMessageOverride
        self.onClearMessageOverride = onClearMessageOverride
        self.onSend = onSend
        self.onStop = onStop
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !visibleSuggestions.isEmpty {
                suggestionMenu
            }

            TextField("Message Wayfinder…", text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.body)
                .lineLimit(1...5)
                .padding(.horizontal, 3)
                .padding(.vertical, 6)
                .focused($focused)
                .accessibilityLabel("Message Wayfinder")
                .accessibilityHint("Press Return to send, or Shift-Return for a new line.")
                .onKeyPress(.downArrow) {
                    guard !visibleSuggestions.isEmpty else { return .ignored }
                    moveSuggestionSelection(by: 1)
                    return .handled
                }
                .onKeyPress(.upArrow) {
                    guard !visibleSuggestions.isEmpty else { return .ignored }
                    moveSuggestionSelection(by: -1)
                    return .handled
                }
                .onKeyPress(.tab) {
                    guard selectCurrentSuggestion() else { return .ignored }
                    return .handled
                }
                .onKeyPress(.escape) {
                    guard !visibleSuggestions.isEmpty else { return .ignored }
                    suggestionsDismissed = true
                    return .handled
                }
                .onKeyPress(.return, phases: .down) { keyPress in
                    if keyPress.modifiers == [.shift] {
                        return .ignored
                    }

                    guard keyPress.modifiers.isEmpty || keyPress.modifiers == [.command] else {
                        return .handled
                    }
                    if selectCurrentSuggestion() {
                        return .handled
                    }
                    guard canSend, !isSending else {
                        return .handled
                    }
                    onSend()
                    return .handled
                }

            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 5) {
                    if let messageOverride {
                        messageOverrideChip(messageOverride)
                    }
                    destinationMenu
                }
                Spacer(minLength: 8)
                Button(action: isSending ? onStop : onSend) {
                    Image(systemName: isSending ? "stop.fill" : "arrow.up")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 30, height: 30)
                        .background((canSend || isSending) ? WayfinderTheme.local : Color.secondary.opacity(0.35), in: Circle())
                }
                .buttonStyle(.plain)
                .disabled(!canSend && !isSending)
                .keyboardShortcut(isSending ? "." : .return, modifiers: .command)
                .accessibilityLabel(isSending ? "Stop response" : "Send message")
                .accessibilityHint(isSending ? "Stops the current model response." : "Press Return to send, or Shift-Return for a new line.")
                .help(isSending ? "Stop response (Command-Period)" : "Send message (Return)")
            }
        }
        .padding(.leading, 8)
        .padding(.trailing, 10)
        .padding(.vertical, 9)
        .background(ChatWorkspaceChrome.composer, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(focused ? WayfinderTheme.local.opacity(0.8) : ChatWorkspaceChrome.border, lineWidth: focused ? 1.25 : 1)
        )
        .frame(maxWidth: ChatWorkspaceChrome.composerWidth)
        .padding(.horizontal, 34)
        .padding(.top, 12)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity)
        .background(ChatWorkspaceChrome.canvas)
        .onAppear {
            focused = true
        }
        .onChange(of: draft) {
            suggestionsDismissed = false
            if !enabledSuggestions.contains(where: { $0.id == selectedSuggestionID }) {
                selectedSuggestionID = enabledSuggestions.first?.id
            }
        }
    }

    private var suggestions: [ChatDestination] {
        ChatDestinationMentionResolver.suggestions(
            for: draft,
            destinations: destinations
        )
    }

    private var visibleSuggestions: [ChatDestination] {
        guard messageOverride == nil, !suggestionsDismissed else { return [] }
        return suggestions
    }

    private var enabledSuggestions: [ChatDestination] {
        visibleSuggestions.filter(\.isAvailable)
    }

    private var suggestionMenu: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(visibleSuggestions) { option in
                    Button {
                        selectSuggestion(option)
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: option.isChatGPTAccount ? "person.crop.circle" : "point.3.connected.trianglepath.dotted")
                                .frame(width: 16)
                                .foregroundStyle(option.isAvailable ? WayfinderTheme.local : .secondary)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(option.title)
                                    .font(.callout.weight(.medium))
                                    .foregroundStyle(.primary)
                                Text(option.detail)
                                    .font(.caption)
                                    .foregroundStyle(ChatWorkspaceChrome.secondaryText)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 10)
                            if !option.isAvailable {
                                Text("Unavailable")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 7)
                        .contentShape(Rectangle())
                        .background(
                            option.id == currentSuggestion?.id
                                ? WayfinderTheme.local.opacity(0.12)
                                : Color.clear,
                            in: RoundedRectangle(cornerRadius: 8, style: .continuous)
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(!option.isAvailable)
                    .accessibilityLabel(option.title)
                    .accessibilityValue(
                        "\(option.detail)\(option.isAvailable ? "" : ", unavailable")"
                    )
                    .accessibilityHint("Routes this message only.")
                }
            }
        }
        .frame(maxHeight: 240)
        .padding(4)
        .background(ChatWorkspaceChrome.mutedFill, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(ChatWorkspaceChrome.border)
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Message destination suggestions")
    }

    private var currentSuggestion: ChatDestination? {
        enabledSuggestions.first(where: { $0.id == selectedSuggestionID })
            ?? enabledSuggestions.first
    }

    private func moveSuggestionSelection(by offset: Int) {
        guard !enabledSuggestions.isEmpty else { return }
        let currentIndex = currentSuggestion.flatMap { current in
            enabledSuggestions.firstIndex(where: { $0.id == current.id })
        } ?? 0
        let nextIndex = (currentIndex + offset + enabledSuggestions.count)
            % enabledSuggestions.count
        selectedSuggestionID = enabledSuggestions[nextIndex].id
    }

    @discardableResult
    private func selectCurrentSuggestion() -> Bool {
        guard let currentSuggestion else { return false }
        selectSuggestion(currentSuggestion)
        return true
    }

    private func selectSuggestion(_ option: ChatDestination) {
        guard option.isAvailable else { return }
        onSelectMessageOverride(option)
        selectedSuggestionID = nil
        suggestionsDismissed = true
        focused = true
    }

    private func messageOverrideChip(_ option: ChatDestination) -> some View {
        Button(action: onClearMessageOverride) {
            HStack(spacing: 5) {
                Text("\(option.title) · this message")
                Image(systemName: "xmark")
                    .font(.caption2.weight(.bold))
            }
            .font(.caption.weight(.medium))
            .foregroundStyle(WayfinderTheme.local)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(WayfinderTheme.local.opacity(0.11), in: Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(isSending)
        .accessibilityLabel("Remove \(option.title) override")
        .accessibilityValue("Routes this message only")
        .help("Remove this-message destination")
    }

    private var destinationMenu: some View {
        Menu {
            ForEach(destinations) { option in
                Button {
                    destination = option
                } label: {
                    Label(
                        option.isAutomatic
                            ? "Automatic — Wayfinder chooses"
                            : "\(option.title) — \(option.detail)",
                        systemImage: option.id == destination.id ? "checkmark" : "circle"
                    )
                }
                .disabled(!option.isAvailable)
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                Text(destination.title)
                    .lineLimit(1)
            }
            .font(.caption.weight(.medium))
            .foregroundStyle(
                destination.isAvailable ? ChatWorkspaceChrome.secondaryText : Color.orange
            )
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.visible)
        .fixedSize()
        .disabled(isSending)
        .accessibilityLabel("Chat destination")
        .accessibilityValue(
            "\(destination.title), \(destination.detail)\(destination.isAvailable ? "" : ", unavailable")"
        )
        .help(
            destination.isAvailable
                ? "Chat destination: \(destination.title) — \(destination.detail)"
                : "\(destination.title) is unavailable. Choose another destination."
        )
    }
}
