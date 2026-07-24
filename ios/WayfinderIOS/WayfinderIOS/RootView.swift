import SwiftUI

struct RootView: View {
  @Environment(AppModel.self) private var appModel
  @Environment(\.horizontalSizeClass) private var horizontalSizeClass
  @State private var showsSidebar = false

  var body: some View {
    if horizontalSizeClass == .regular {
      regularWidthLayout
    } else {
      compactWidthLayout
    }
  }

  private var compactWidthLayout: some View {
    @Bindable var appModel = appModel

    return GeometryReader { proxy in
      ZStack(alignment: .leading) {
        TabView(selection: $appModel.selectedTab) {
          ChatTabView(openSidebar: openSidebar)
            .tag(AppTab.chat)

          NavigationStack {
            ThreadsView(openSidebar: openSidebar)
          }
          .tag(AppTab.threads)

          NavigationStack {
            DestinationsView(openSidebar: openSidebar)
          }
          .tag(AppTab.destinations)

          NavigationStack {
            SettingsView(openSidebar: openSidebar)
          }
          .tag(AppTab.settings)
        }
        .toolbar(.hidden, for: .tabBar)
        .allowsHitTesting(!showsSidebar)
        .accessibilityHidden(showsSidebar)

        if showsSidebar {
          Color.black.opacity(0.28)
            .ignoresSafeArea()
            .onTapGesture(perform: closeSidebar)
            .transition(.opacity)
            .accessibilityHidden(true)

          AppSidebarView(select: select)
            .frame(width: min(340, proxy.size.width * 0.86))
            .transition(.move(edge: .leading))
            .accessibilityAddTraits(.isModal)
        }
      }
    }
    .animation(.snappy(duration: 0.28), value: showsSidebar)
  }

  private var regularWidthLayout: some View {
    NavigationSplitView {
      AppSidebarView(select: select)
        .navigationSplitViewColumnWidth(min: 260, ideal: 300, max: 340)
    } detail: {
      selectedDetail
    }
    .navigationSplitViewStyle(.balanced)
  }

  @ViewBuilder
  private var selectedDetail: some View {
    switch appModel.selectedTab {
    case .chat:
      ChatTabView()
    case .threads:
      NavigationStack {
        ThreadsView()
      }
    case .destinations:
      NavigationStack {
        DestinationsView()
      }
    case .settings:
      NavigationStack {
        SettingsView()
      }
    }
  }

  private func openSidebar() {
    showsSidebar = true
  }

  private func closeSidebar() {
    showsSidebar = false
  }

  private func select(_ tab: AppTab) {
    appModel.selectedTab = tab
    closeSidebar()
  }
}

private struct AppSidebarView: View {
  @Environment(AppModel.self) private var appModel
  let select: (AppTab) -> Void

  var body: some View {
    VStack(spacing: 0) {
      sidebarHeader

      ScrollView {
        VStack(alignment: .leading, spacing: 18) {
          Button {
            appModel.startNewChat()
            select(.chat)
          } label: {
            Label("New chat", systemImage: "square.and.pencil")
              .frame(maxWidth: .infinity, alignment: .leading)
              .contentShape(Rectangle())
          }
          .buttonStyle(.plain)
          .font(.body.weight(.medium))
          .accessibilityHint("Starts a new conversation")

          VStack(alignment: .leading, spacing: 8) {
            Text("Chats")
              .font(.caption.weight(.semibold))
              .foregroundStyle(.secondary)
              .textCase(.uppercase)

            if let prompt = appModel.submittedPrompt {
              Button {
                select(.chat)
              } label: {
                VStack(alignment: .leading, spacing: 3) {
                  Text(prompt)
                    .lineLimit(1)
                  Text("Current chat")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(
                  appModel.selectedTab == .chat
                    ? Color.primary.opacity(0.07)
                    : Color.clear,
                  in: RoundedRectangle(cornerRadius: 10)
                )
              }
              .buttonStyle(.plain)
            } else {
              Text("Your conversations will appear here.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .padding(.vertical, 8)
            }

            SidebarDestinationButton(
              title: "All chats",
              systemImage: AppTab.threads.systemImage,
              isSelected: appModel.selectedTab == .threads
            ) {
              select(.threads)
            }
          }

          Divider()

          SidebarDestinationButton(
            title: "Destinations",
            systemImage: AppTab.destinations.systemImage,
            isSelected: appModel.selectedTab == .destinations
          ) {
            select(.destinations)
          }

          SidebarDestinationButton(
            title: "Settings",
            systemImage: AppTab.settings.systemImage,
            isSelected: appModel.selectedTab == .settings
          ) {
            select(.settings)
          }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
      }

      Divider()

      Button {
        select(.settings)
      } label: {
        Label {
          VStack(alignment: .leading, spacing: 2) {
            Text(appModel.privacyPosture.title)
              .font(.subheadline.weight(.medium))
            Text(appModel.privacyPosture.boundarySummary)
              .font(.caption)
              .foregroundStyle(.secondary)
              .lineLimit(1)
          }
        } icon: {
          Image(systemName: "hand.raised.fill")
            .foregroundStyle(WayfinderTheme.accent)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .padding(16)
    }
    .background(Color(uiColor: .secondarySystemBackground))
  }

  private var sidebarHeader: some View {
    HStack {
      WayfinderMark()

      Text("Wayfinder")
        .font(.headline)

      Spacer()
    }
    .padding(.horizontal, 16)
    .frame(height: 56)
  }
}

private struct SidebarDestinationButton: View {
  let title: String
  let systemImage: String
  let isSelected: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      Label(title, systemImage: systemImage)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(
          isSelected ? Color.primary.opacity(0.07) : Color.clear,
          in: RoundedRectangle(cornerRadius: 10)
        )
        .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
  }
}

struct SidebarToolbarButton: ToolbarContent {
  let action: () -> Void

  var body: some ToolbarContent {
    ToolbarItem(placement: .topBarLeading) {
      Button(action: action) {
        Image(systemName: "line.3.horizontal")
      }
      .accessibilityLabel("Open navigation")
    }
  }
}

struct WayfinderMark: View {
  var body: some View {
    Image(systemName: "point.3.connected.trianglepath.dotted")
      .font(.body.weight(.semibold))
      .foregroundStyle(WayfinderTheme.accent)
      .accessibilityHidden(true)
  }
}
