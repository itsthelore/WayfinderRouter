import SwiftUI

struct DestinationsView: View {
  @Environment(AppModel.self) private var appModel

  var body: some View {
    List {
      Section {
        Text(
          "These are deterministic routing candidates for the bridge smoke test. They do not execute requests."
        )
        .font(.footnote)
        .foregroundStyle(.secondary)
      }

      Section("On this device") {
        destinationRow(appModel.destinations[0], systemImage: "iphone")
      }

      Section("Direct cloud") {
        destinationRow(appModel.destinations[1], systemImage: "cloud")
      }
    }
    .navigationTitle("Destinations")
  }

  private func destinationRow(
    _ destination: PreviewDestination,
    systemImage: String
  ) -> some View {
    Label {
      VStack(alignment: .leading, spacing: 3) {
        Text(destination.displayName)
        Text(destination.detail)
          .font(.caption)
          .foregroundStyle(.secondary)
      }
    } icon: {
      Image(systemName: systemImage)
        .foregroundStyle(WayfinderTheme.accent)
    }
  }
}
