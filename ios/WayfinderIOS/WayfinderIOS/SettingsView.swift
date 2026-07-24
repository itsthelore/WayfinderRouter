import SwiftUI

struct SettingsView: View {
  @Environment(AppModel.self) private var appModel

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
    }
    .navigationTitle("Settings")
  }
}
