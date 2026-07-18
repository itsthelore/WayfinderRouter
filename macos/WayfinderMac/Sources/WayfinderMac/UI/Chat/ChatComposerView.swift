import SwiftUI

public struct ChatComposerView: View {
    @Binding var draft: String
    @Binding var destination: ChatDestination
    let destinations: [ChatDestination]
    let isSending: Bool
    let canSend: Bool
    let onSend: () -> Void
    let onStop: () -> Void

    @FocusState private var focused: Bool

    public init(
        draft: Binding<String>,
        destination: Binding<ChatDestination>,
        destinations: [ChatDestination],
        isSending: Bool,
        canSend: Bool,
        onSend: @escaping () -> Void,
        onStop: @escaping () -> Void
    ) {
        self._draft = draft
        self._destination = destination
        self.destinations = destinations
        self.isSending = isSending
        self.canSend = canSend
        self.onSend = onSend
        self.onStop = onStop
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            TextField("Message Wayfinder…", text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.body)
                .lineLimit(1...5)
                .padding(.horizontal, 3)
                .padding(.vertical, 6)
                .focused($focused)
                .accessibilityLabel("Message Wayfinder")
                .accessibilityHint("Press Return to send, or Shift-Return for a new line.")
                .onKeyPress(.return, phases: .down) { keyPress in
                    if keyPress.modifiers == [.shift] {
                        return .ignored
                    }

                    guard keyPress.modifiers.isEmpty || keyPress.modifiers == [.command] else {
                        return .handled
                    }
                    guard canSend, !isSending else {
                        return .handled
                    }
                    onSend()
                    return .handled
                }

            HStack(alignment: .center, spacing: 10) {
                destinationMenu
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
