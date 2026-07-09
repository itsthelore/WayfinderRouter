import AppKit
import WayfinderMacCore

@main
enum WayfinderMacMain {
    @MainActor private static var appDelegate: AppDelegate?

    @MainActor
    static func main() {
        let delegate = AppDelegate(client: GatewayWayfinderClient())
        appDelegate = delegate

        let app = NSApplication.shared
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}
