import SwiftUI

public struct ChatComposerView: View {
    @Binding var draft: String
    let isSending: Bool
    let canSend: Bool
    let onSend: () -> Void
    let onStop: () -> Void

    @FocusState private var focused: Bool

    public init(
        draft: Binding<String>,
        isSending: Bool,
        canSend: Bool,
        onSend: @escaping () -> Void,
        onStop: @escaping () -> Void
    ) {
        self._draft = draft
        self.isSending = isSending
        self.canSend = canSend
        self.onSend = onSend
        self.onStop = onStop
    }

    public var body: some View {
        VStack(spacing: 7) {
            HStack(alignment: .bottom, spacing: 10) {
                TextField("Message Wayfinder...", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.body)
                    .lineLimit(1...5)
                    .padding(.horizontal, 3)
                    .padding(.vertical, 6)
                    .focused($focused)

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
                .accessibilityHint(isSending ? "Stops the current model response." : "Routes this message through Wayfinder.")
                .help(isSending ? "Stop response (Command-Period)" : "Send message (Command-Return)")
            }
            .padding(.leading, 8)
            .padding(.trailing, 10)
            .padding(.vertical, 9)
            .background(ChatWorkspaceChrome.composer, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(focused ? WayfinderTheme.local.opacity(0.8) : ChatWorkspaceChrome.border, lineWidth: focused ? 1.25 : 1)
            )

            HStack {
                Label("Routes through your local gateway", systemImage: "checkmark.shield")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
                Text("⌘↩ sends")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 4)
        }
        .frame(maxWidth: ChatWorkspaceChrome.composerWidth)
        .padding(.horizontal, 34)
        .padding(.top, 10)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity)
        .background(ChatWorkspaceChrome.canvas)
        .onAppear {
            focused = true
        }
    }
}
