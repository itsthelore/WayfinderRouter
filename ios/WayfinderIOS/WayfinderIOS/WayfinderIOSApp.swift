import SwiftUI

@main
struct WayfinderIOSApp: App {
  @State private var appModel = AppModel()

  var body: some Scene {
    WindowGroup {
      RootView()
        .environment(appModel)
        .tint(WayfinderTheme.accent)
    }
  }
}
