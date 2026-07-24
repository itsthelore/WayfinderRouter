import SwiftUI

struct SettingsView: View {
  @Environment(AppModel.self) private var appModel
  @State private var exportedConversations: Data?
  @State private var showsClearConfirmation = false
  var openSidebar: (() -> Void)?

  var body: some View {
    @Bindable var appModel = appModel

    Form {
      Section("Privacy") {
        Picker("Maximum execution boundary", selection: $appModel.privacyPosture) {
          ForEach(PrivacyPostureOption.allCases) { posture in
            Text(posture.title).tag(posture)
          }
        }

        Text(appModel.privacyPosture.boundarySummary)
          .font(.footnote)
          .foregroundStyle(.secondary)
      }

      Section("Runtime") {
        LabeledContent("Router", value: "Embedded Rust core")
        LabeledContent("Provider execution", value: "Not in this build slice")
        LabeledContent("Mac required", value: "No")
      }

      Section {
        Picker("Keep conversations", selection: retentionBinding) {
          ForEach(ConversationRetentionPolicy.allCases) { policy in
            Text(policy.title).tag(policy)
          }
        }

        if let exportedConversations {
          ShareLink(
            item: exportedConversations,
            preview: SharePreview("Wayfinder conversations.json")
          ) {
            Label("Share Export", systemImage: "square.and.arrow.up")
          }
        } else {
          Button {
            Task {
              exportedConversations = await appModel.exportConversations()
            }
          } label: {
            Label("Prepare Export", systemImage: "doc.badge.arrow.up")
          }
        }

        Button(role: .destructive) {
          showsClearConfirmation = true
        } label: {
          Label("Clear All Conversations", systemImage: "trash")
        }
        .disabled(appModel.threads.isEmpty && appModel.draft.isEmpty)
      } header: {
        Text("Conversations")
      } footer: {
        Text(
          "Exports contain conversation text and route receipts, never provider credentials."
        )
      }
    }
    .navigationTitle("Settings")
    .toolbar {
      if let openSidebar {
        SidebarToolbarButton(action: openSidebar)
      }
    }
    .confirmationDialog(
      "Clear all conversations?",
      isPresented: $showsClearConfirmation,
      titleVisibility: .visible
    ) {
      Button("Clear All Conversations", role: .destructive) {
        Task {
          await appModel.deleteAllThreads()
          exportedConversations = nil
        }
      }
      Button("Cancel", role: .cancel) {}
    } message: {
      Text("This permanently removes saved threads and the current draft.")
    }
  }

  private var retentionBinding: Binding<ConversationRetentionPolicy> {
    Binding(
      get: { appModel.retentionPolicy },
      set: { policy in
        Task {
          await appModel.setRetentionPolicy(policy)
          exportedConversations = nil
        }
      }
    )
  }
}
