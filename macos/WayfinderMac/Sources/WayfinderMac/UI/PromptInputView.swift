import SwiftUI

public struct PromptInputView: View {
    @Binding private var prompt: String
    private let isAnalysing: Bool
    @FocusState private var isFocused: Bool

    public init(prompt: Binding<String>, isAnalysing: Bool) {
        self._prompt = prompt
        self.isAnalysing = isAnalysing
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Prompt")
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundStyle(.secondary)

            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color(nsColor: .textBackgroundColor))
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                    )

                TextEditor(text: $prompt)
                    .font(.body)
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .focused($isFocused)
                    .disabled(isAnalysing)

                if prompt.isEmpty {
                    Text("Paste or type a prompt")
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 13)
                        .padding(.vertical, 15)
                        .allowsHitTesting(false)
                }
            }
            .frame(height: 150)
        }
        .onAppear {
            DispatchQueue.main.async {
                isFocused = true
            }
        }
    }
}
