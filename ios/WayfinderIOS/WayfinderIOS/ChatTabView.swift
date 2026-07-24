import SwiftUI

struct ChatTabView: View {
  var openSidebar: (() -> Void)?

  var body: some View {
    NavigationStack {
      ChatView(openSidebar: openSidebar)
    }
  }
}

struct ChatView: View {
  @Environment(AppModel.self) private var appModel
  @FocusState private var composerFocused: Bool
  @State private var presentedReceipt: RoutePreview?

  var openSidebar: (() -> Void)?

  var body: some View {
    @Bindable var appModel = appModel

    ScrollView {
      LazyVStack(spacing: 26) {
        if let thread = appModel.activeThread,
          !thread.messages.isEmpty
        {
          ConversationTranscript(
            thread: thread,
            showReceipt: { presentedReceipt = $0 }
          )
        } else {
          ChatEmptyState(useSuggestion: useSuggestion)
            .containerRelativeFrame(.vertical) { length, _ in
              max(300, length * 0.62)
            }
        }
      }
      .frame(maxWidth: 760)
      .frame(maxWidth: .infinity)
      .padding(.horizontal, 20)
      .padding(.bottom, 24)
    }
    .scrollDismissesKeyboard(.interactively)
    .background(Color(uiColor: .systemBackground))
    .safeAreaInset(edge: .bottom, spacing: 0) {
      ComposerView(
        draft: $appModel.draft,
        privacyPosture: $appModel.privacyPosture,
        canSubmit: appModel.canPreviewRoute,
        submit: {
          Task {
            await appModel.previewRoute()
          }
        }
      )
      .focused($composerFocused)
      .frame(maxWidth: 780)
      .padding(.horizontal, 12)
      .padding(.top, 8)
      .padding(.bottom, 8)
      .frame(maxWidth: .infinity)
      .background(.ultraThinMaterial)
    }
    .navigationTitle("Wayfinder")
    .navigationBarTitleDisplayMode(.inline)
    .toolbar {
      if let openSidebar {
        SidebarToolbarButton(action: openSidebar)
      }

      ToolbarItem(placement: .principal) {
        Menu {
          Button("Automatic — Wayfinder chooses") {}
            .disabled(true)
          Divider()
          Text(appModel.privacyPosture.title)
        } label: {
          HStack(spacing: 5) {
            Text("Wayfinder")
              .font(.headline)
            Image(systemName: "chevron.down")
              .font(.caption2.weight(.bold))
              .foregroundStyle(.secondary)
          }
        }
        .accessibilityLabel("Wayfinder routing mode")
        .accessibilityValue("Automatic")
      }

      ToolbarItemGroup(placement: .topBarTrailing) {
        Button {
          Task {
            await appModel.startNewChat()
            composerFocused = true
          }
        } label: {
          Image(systemName: "square.and.pencil")
        }
        .accessibilityLabel("New chat")
      }
    }
    .sheet(item: $presentedReceipt) { receipt in
      RouteReceiptSheet(receipt: receipt)
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
    }
    .onChange(of: appModel.draft) {
      appModel.scheduleDraftSave()
    }
    .task {
      await appModel.restoreConversations()
    }
  }

  private func useSuggestion(_ prompt: String) {
    appModel.draft = prompt
    composerFocused = true
  }
}

private struct ChatEmptyState: View {
  let useSuggestion: (String) -> Void

  private let suggestions = [
    "Help me plan a focused workday",
    "Explain a difficult idea simply",
    "Draft a thoughtful reply",
  ]

  var body: some View {
    VStack(spacing: 22) {
      WayfinderMark()
        .font(.system(size: 30, weight: .semibold))

      Text("What can I help with?")
        .font(.title2.weight(.semibold))
        .multilineTextAlignment(.center)

      ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 10) {
          ForEach(suggestions, id: \.self) { suggestion in
            Button(suggestion) {
              useSuggestion(suggestion)
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.capsule)
            .tint(.primary)
          }
        }
        .padding(.horizontal, 2)
      }
      .frame(maxWidth: 560)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .padding(.top, 32)
  }
}

private struct ConversationTranscript: View {
  let thread: ConversationThreadSnapshot
  let showReceipt: (RoutePreview) -> Void

  var body: some View {
    VStack(spacing: 30) {
      ForEach(thread.messages) { message in
        VStack(spacing: 16) {
          HStack {
            Spacer(minLength: 48)
            Text(message.content)
              .padding(.horizontal, 16)
              .padding(.vertical, 11)
              .background(
                Color(uiColor: .secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 20)
              )
          }
          .accessibilityElement(children: .combine)
          .accessibilityLabel("You")
          .accessibilityValue(message.content)

          Text(
            "Wayfinder calculated where this message would run. This preview did not contact a model."
          )
          .frame(maxWidth: .infinity, alignment: .leading)

          if let receipt = message.routeReceipt {
            Button {
              showReceipt(receipt.routePreview)
            } label: {
              HStack(spacing: 7) {
                Image(systemName: boundaryImage(for: receipt))
                  .foregroundStyle(WayfinderTheme.accent)
                Text("Would run \(receipt.executionSummary.lowercased())")
                  .fontWeight(.medium)
                Image(systemName: "info.circle")
                  .foregroundStyle(.secondary)
              }
              .font(.footnote)
            }
            .buttonStyle(.plain)
            .accessibilityHint("Shows routing details")
            .frame(maxWidth: .infinity, alignment: .leading)
          } else if message.status == .failed {
            Label(
              "No eligible route",
              systemImage: "exclamationmark.triangle.fill"
            )
            .font(.footnote.weight(.medium))
            .foregroundStyle(.orange)
            .frame(maxWidth: .infinity, alignment: .leading)
          }
        }
      }
    }
    .padding(.top, 24)
  }

  private func boundaryImage(for receipt: StoredRouteReceipt) -> String {
    receipt.destinationID == "device-preview" ? "iphone" : "cloud"
  }
}

extension StoredRouteReceipt {
  fileprivate var routePreview: RoutePreview {
    RoutePreview(
      destinationID: destinationID,
      destinationName: destinationName,
      score: score,
      recommendation: recommendation,
      executionSummary: executionSummary
    )
  }
}

private struct RouteReceiptSheet: View {
  @Environment(\.dismiss) private var dismiss
  let receipt: RoutePreview

  var body: some View {
    NavigationStack {
      List {
        Section {
          LabeledContent("Destination", value: receipt.destinationName)
          LabeledContent("Runs", value: receipt.executionSummary)
          LabeledContent("Routing tier", value: receipt.recommendation)
          LabeledContent(
            "Score",
            value: receipt.score.formatted(.number.precision(.fractionLength(2)))
          )
        }

        Section {
          Label(
            "No provider was contacted and no message left this device.",
            systemImage: "checkmark.shield"
          )
          .foregroundStyle(.secondary)
        } header: {
          Text("This build slice")
        }
      }
      .navigationTitle("Routing details")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .confirmationAction) {
          Button("Done") {
            dismiss()
          }
        }
      }
    }
  }
}

private struct ComposerView: View {
  @Binding var draft: String
  @Binding var privacyPosture: PrivacyPostureOption
  let canSubmit: Bool
  let submit: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      TextField("Message Wayfinder", text: $draft, axis: .vertical)
        .lineLimit(1...6)
        .textFieldStyle(.plain)
        .font(.body)
        .accessibilityLabel("Message Wayfinder")
        .submitLabel(.send)
        .onSubmit {
          if canSubmit {
            submit()
          }
        }

      HStack(spacing: 10) {
        Menu {
          Button("Attachments are not available in this build") {}
            .disabled(true)
        } label: {
          Image(systemName: "plus")
            .frame(width: 32, height: 32)
            .background(
              Color(uiColor: .tertiarySystemFill),
              in: Circle()
            )
        }
        .accessibilityLabel("Add")
        .accessibilityHint("Attachments are not available in this build")

        Label("Automatic", systemImage: "point.3.connected.trianglepath.dotted")
          .font(.subheadline.weight(.medium))
          .foregroundStyle(.secondary)

        Spacer(minLength: 8)

        Menu {
          Picker("Privacy", selection: $privacyPosture) {
            ForEach(PrivacyPostureOption.allCases) { posture in
              Text(posture.title).tag(posture)
            }
          }
        } label: {
          Image(systemName: "hand.raised")
            .frame(width: 32, height: 32)
        }
        .accessibilityLabel("Privacy")
        .accessibilityValue(privacyPosture.title)

        Button(action: submit) {
          Image(systemName: "arrow.up")
            .font(.headline)
            .foregroundStyle(canSubmit ? Color.white : Color.secondary)
            .frame(width: 34, height: 34)
            .background(
              canSubmit ? WayfinderTheme.accent : Color(uiColor: .tertiarySystemFill),
              in: Circle()
            )
        }
        .buttonStyle(.plain)
        .disabled(!canSubmit)
        .accessibilityLabel("Preview route")
      }
    }
    .padding(.horizontal, 14)
    .padding(.vertical, 12)
    .background(
      Color(uiColor: .secondarySystemBackground),
      in: RoundedRectangle(cornerRadius: 24)
    )
    .overlay {
      RoundedRectangle(cornerRadius: 24)
        .stroke(Color.primary.opacity(0.08), lineWidth: 1)
    }
    .shadow(color: .black.opacity(0.08), radius: 14, y: 5)
  }
}
