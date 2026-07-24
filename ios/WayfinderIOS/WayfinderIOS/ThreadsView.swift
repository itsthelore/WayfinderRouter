import SwiftUI

struct ThreadsView: View {
  @Environment(AppModel.self) private var appModel
  var openSidebar: (() -> Void)?

  var body: some View {
    Group {
      if appModel.isRestoringConversations {
        ProgressView("Restoring conversations")
      } else if appModel.threads.isEmpty {
        ContentUnavailableView(
          "No chats yet",
          systemImage: "bubble.left.and.bubble.right",
          description: Text("Start a chat and it will appear here.")
        )
      } else {
        List {
          ForEach(appModel.threads) { thread in
            Button {
              Task {
                await appModel.selectThread(id: thread.id)
              }
            } label: {
              VStack(alignment: .leading, spacing: 5) {
                Text(thread.title)
                  .font(.body.weight(.medium))
                  .foregroundStyle(.primary)
                  .lineLimit(2)

                HStack {
                  Text(
                    "\(thread.messages.count) \(thread.messages.count == 1 ? "turn" : "turns")"
                  )
                  Spacer()
                  Text(
                    thread.updatedAt.formatted(
                      date: .abbreviated,
                      time: .shortened
                    )
                  )
                }
                .font(.caption)
                .foregroundStyle(.secondary)
              }
              .padding(.vertical, 4)
            }
            .buttonStyle(.plain)
          }
          .onDelete { offsets in
            let ids = offsets.map { appModel.threads[$0].id }
            for id in ids {
              Task {
                await appModel.deleteThread(id: id)
              }
            }
          }
        }
      }
    }
    .navigationTitle("Threads")
    .toolbar {
      if let openSidebar {
        SidebarToolbarButton(action: openSidebar)
      }
    }
    .task {
      await appModel.restoreConversations()
    }
  }
}
