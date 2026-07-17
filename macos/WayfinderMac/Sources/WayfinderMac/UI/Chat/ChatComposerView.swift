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
        VStack(spacing: 8) {
            HStack(alignment: .center, spacing: 10) {
                ZStack(alignment: .topLeading) {
                    RoundedRectangle(cornerRadius: 11, style: .continuous)
                        .fill(Color(nsColor: .textBackgroundColor).opacity(0.92))
                        .overlay(
                            RoundedRectangle(cornerRadius: 11, style: .continuous)
                                .stroke(focused ? WayfinderTheme.local : WayfinderTheme.hairline, lineWidth: focused ? 1.35 : 1)
                        )

                    TextEditor(text: $draft)
                        .font(.callout)
                        .scrollContentBackground(.hidden)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 5)
                        .focused($focused)
                        .frame(minHeight: 42, maxHeight: 68)

                    if draft.isEmpty {
                        Text("Message Wayfinder...")
                            .font(.callout)
                            .foregroundStyle(.tertiary)
                            .padding(.horizontal, 13)
                            .padding(.vertical, 13)
                            .allowsHitTesting(false)
                    }
                }
                .frame(height: 68)

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

            HStack {
                Label("Messages route through your local Wayfinder gateway", systemImage: "checkmark.shield")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
                Text("⌘↩ sends")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
        .background(.bar)
        .onAppear {
            focused = true
        }
    }
}
