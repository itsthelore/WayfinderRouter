import SwiftUI

struct RootView: View {
  @Environment(AppModel.self) private var appModel

  var body: some View {
    @Bindable var appModel = appModel

    TabView(selection: $appModel.selectedTab) {
      ChatTabView()
        .tabItem {
          Label(AppTab.chat.title, systemImage: AppTab.chat.systemImage)
        }
        .tag(AppTab.chat)

      NavigationStack {
        ThreadsView()
      }
      .tabItem {
        Label(AppTab.threads.title, systemImage: AppTab.threads.systemImage)
      }
      .tag(AppTab.threads)

      NavigationStack {
        DestinationsView()
      }
      .tabItem {
        Label(
          AppTab.destinations.title,
          systemImage: AppTab.destinations.systemImage
        )
      }
      .tag(AppTab.destinations)

      NavigationStack {
        SettingsView()
      }
      .tabItem {
        Label(AppTab.settings.title, systemImage: AppTab.settings.systemImage)
      }
      .tag(AppTab.settings)
    }
  }
}
