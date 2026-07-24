import SwiftUI

struct ThreadsView: View {
  var openSidebar: (() -> Void)?

  var body: some View {
    ContentUnavailableView(
      "No threads yet",
      systemImage: "clock",
      description: Text(
        "Conversation persistence lands in the next isolated mobile review boundary."
      )
    )
    .navigationTitle("Threads")
    .toolbar {
      if let openSidebar {
        SidebarToolbarButton(action: openSidebar)
      }
    }
  }
}
