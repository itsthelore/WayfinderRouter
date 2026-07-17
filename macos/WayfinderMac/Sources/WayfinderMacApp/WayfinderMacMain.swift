import AppKit
import WayfinderMacCore

@main
enum WayfinderMacMain {
    @MainActor private static var appDelegate: AppDelegate?

    @MainActor
    static func main() {
        let arguments = CommandLine.arguments
        let client: any WayfinderClient
        if arguments.contains("--preview-chat") {
            client = MockWayfinderClient()
        } else {
            client = GatewayWayfinderClient()
        }
        let delegate = AppDelegate(
            client: client,
            openChatOnLaunch: arguments.contains("--open-chat") || arguments.contains("--preview-chat")
        )
        appDelegate = delegate

        let app = NSApplication.shared
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}
