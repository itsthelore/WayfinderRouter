import SwiftUI

struct ChatTabView: View {
  @Environment(\.horizontalSizeClass) private var horizontalSizeClass

  var body: some View {
    if horizontalSizeClass == .regular {
      NavigationSplitView {
        MobileSidebarView()
      } detail: {
        NavigationStack {
          ChatView()
        }
      }
    } else {
      NavigationStack {
        ChatView()
      }
    }
  }
}

private struct MobileSidebarView: View {
  @Environment(AppModel.self) private var appModel

  var body: some View {
    List {
      Section("Threads") {
        ContentUnavailableView(
          "No threads yet",
          systemImage: "bubble.left.and.bubble.right",
          description: Text("Completed conversations will appear here.")
        )
      }

      Section("Routing candidates") {
        ForEach(appModel.destinations) { destination in
          Label {
            VStack(alignment: .leading, spacing: 2) {
              Text(destination.displayName)
              Text(destination.boundaryLabel)
                .font(.caption)
                .foregroundStyle(.secondary)
            }
          } icon: {
            Image(
              systemName: destination.boundary == .onDevice
                ? "iphone"
                : "cloud"
            )
          }
        }
      }
    }
    .navigationTitle("Wayfinder")
  }
}

struct ChatView: View {
  @Environment(AppModel.self) private var appModel
  @FocusState private var composerFocused: Bool

  var body: some View {
    @Bindable var appModel = appModel

    VStack(spacing: 0) {
      ScrollView {
        VStack(spacing: 24) {
          if case .idle = appModel.routePreviewState {
            ChatEmptyState()
              .padding(.top, 72)
          } else {
            RoutePreviewCard(state: appModel.routePreviewState)
              .padding(.top, 24)
          }
        }
        .frame(maxWidth: 720)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 20)
      }

      ComposerView(
        draft: $appModel.draft,
        privacyPosture: $appModel.privacyPosture,
        canSubmit: appModel.canPreviewRoute,
        submit: appModel.previewRoute
      )
      .focused($composerFocused)
      .frame(maxWidth: 760)
      .padding(.horizontal, 16)
      .padding(.vertical, 12)
    }
    .background(Color(uiColor: .systemGroupedBackground))
    .navigationTitle("New chat")
    .navigationBarTitleDisplayMode(.inline)
    .onChange(of: appModel.draft) {
      appModel.clearPreview()
    }
  }
}

private struct ChatEmptyState: View {
  var body: some View {
    ContentUnavailableView {
      Label(
        "Where should this request run?",
        systemImage: "point.3.connected.trianglepath.dotted"
      )
    } description: {
      Text(
        "Write a message and Wayfinder will preview the route chosen by its embedded Rust core."
      )
    }
  }
}

private struct RoutePreviewCard: View {
  let state: RoutePreviewState

  var body: some View {
    Group {
      switch state {
      case .idle:
        EmptyView()
      case .routed(let preview):
        VStack(alignment: .leading, spacing: 14) {
          Label("Route calculated on this device", systemImage: "checkmark.circle.fill")
            .font(.headline)
            .foregroundStyle(WayfinderTheme.accent)

          Text(preview.destinationName)
            .font(.title2.weight(.semibold))

          LabeledContent("Execution boundary", value: preview.executionSummary)
          LabeledContent("Routing tier", value: preview.recommendation)
          LabeledContent(
            "Deterministic score",
            value: preview.score.formatted(.number.precision(.fractionLength(2)))
          )

          Divider()

          Text(
            "Routing preview only. No provider was contacted and no message left this device."
          )
          .font(.footnote)
          .foregroundStyle(.secondary)
        }
        .padding(20)
        .background(.background, in: RoundedRectangle(cornerRadius: 20))
        .overlay {
          RoundedRectangle(cornerRadius: 20)
            .stroke(Color(uiColor: .separator).opacity(0.35))
        }
      case .unavailable(let message):
        ContentUnavailableView(
          "No eligible route",
          systemImage: "exclamationmark.triangle",
          description: Text(message)
        )
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
    VStack(alignment: .leading, spacing: 12) {
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

      HStack {
        Menu {
          Picker("Privacy", selection: $privacyPosture) {
            ForEach(PrivacyPostureOption.allCases) { posture in
              Text(posture.title).tag(posture)
            }
          }
        } label: {
          Label(privacyPosture.title, systemImage: "hand.raised")
            .font(.subheadline.weight(.medium))
        }

        Spacer()

        Button(action: submit) {
          Image(systemName: "arrow.up")
            .font(.headline)
            .frame(width: 34, height: 34)
        }
        .buttonStyle(.borderedProminent)
        .buttonBorderShape(.circle)
        .disabled(!canSubmit)
        .accessibilityLabel("Preview route")
      }
    }
    .padding(14)
    .background(.background, in: RoundedRectangle(cornerRadius: 20))
    .overlay {
      RoundedRectangle(cornerRadius: 20)
        .stroke(WayfinderTheme.accent.opacity(0.7), lineWidth: 1.5)
    }
    .shadow(color: .black.opacity(0.06), radius: 18, y: 6)
  }
}
