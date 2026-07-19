import Foundation
import XCTest
@testable import WayfinderMacCore

final class GatewayReplacementReconcilerTests: XCTestCase {
    private actor Recorder {
        var restarts: [URL] = []
        var markers: [String] = []

        func restart(_ gateway: URL) { restarts.append(gateway) }
        func write(_ marker: String) { markers.append(marker) }
        func snapshot() -> (restarts: [URL], markers: [String]) { (restarts, markers) }
    }

    func testChangedInstallationRestartsMatchingLoadedBundledGatewayAndPersistsMarker() async {
        let gateway = URL(fileURLWithPath: "/Applications/Wayfinder.app/Contents/Helpers/WayfinderGateway.app/Contents/MacOS/wayfinder-router")
        let recorder = Recorder()
        let reconciler = makeReconciler(
            gateway: gateway,
            storedMarker: "old",
            fingerprint: "new",
            recorder: recorder
        )

        let result = await reconciler.reconcile()
        let snapshot = await recorder.snapshot()
        XCTAssertEqual(result, .restarted)
        XCTAssertEqual(snapshot.restarts, [gateway])
        XCTAssertEqual(snapshot.markers, ["new"])
    }

    func testUnchangedInstallationDoesNotQueryOrRestartService() async {
        let gateway = URL(fileURLWithPath: "/Applications/Wayfinder.app/Contents/MacOS/wayfinder-router")
        let recorder = Recorder()
        let reconciler = GatewayReplacementReconciler(
            locateGateway: { gateway },
            statusQuery: { _ in XCTFail("Unchanged installation must not query service"); return Self.status(gateway: gateway) },
            restart: { _ in XCTFail("Unchanged installation must not restart") },
            fingerprintProvider: { _ in "same" },
            markerReader: { "same" },
            markerWriter: { _ in XCTFail("Unchanged installation must not rewrite marker") }
        )

        let result = await reconciler.reconcile()
        let snapshot = await recorder.snapshot()
        XCTAssertEqual(result, .unchanged)
        XCTAssertTrue(snapshot.restarts.isEmpty)
    }

    func testMismatchedStoppedOrUninstalledServiceDefersWithoutMarker() async {
        let expected = URL(fileURLWithPath: "/Applications/Wayfinder.app/Contents/MacOS/wayfinder-router")
        let other = URL(fileURLWithPath: "/opt/homebrew/bin/wayfinder-router")
        for status in [
            Self.status(gateway: other),
            Self.status(gateway: expected, loaded: false),
            Self.status(gateway: expected, installed: false),
        ] {
            let recorder = Recorder()
            let reconciler = GatewayReplacementReconciler(
                locateGateway: { expected },
                statusQuery: { _ in status },
                restart: { gateway in await recorder.restart(gateway) },
                fingerprintProvider: { _ in "new" },
                markerReader: { "old" },
                markerWriter: { marker in await recorder.write(marker) }
            )

            let result = await reconciler.reconcile()
            let snapshot = await recorder.snapshot()
            XCTAssertEqual(result, .deferred)
            XCTAssertTrue(snapshot.restarts.isEmpty)
            XCTAssertTrue(snapshot.markers.isEmpty)
        }
    }

    func testFailedRestartDefersAndDoesNotPersistMarker() async {
        let gateway = URL(fileURLWithPath: "/Applications/Wayfinder.app/Contents/MacOS/wayfinder-router")
        let recorder = Recorder()
        let reconciler = GatewayReplacementReconciler(
            locateGateway: { gateway },
            statusQuery: { _ in Self.status(gateway: gateway) },
            restart: { _ in throw GatewayServiceControllerError.restartFailed("expected") },
            fingerprintProvider: { _ in "new" },
            markerReader: { "old" },
            markerWriter: { marker in await recorder.write(marker) }
        )

        let result = await reconciler.reconcile()
        let snapshot = await recorder.snapshot()
        XCTAssertEqual(result, .deferred)
        XCTAssertTrue(snapshot.markers.isEmpty)
    }

    private func makeReconciler(
        gateway: URL,
        storedMarker: String?,
        fingerprint: String,
        recorder: Recorder
    ) -> GatewayReplacementReconciler {
        GatewayReplacementReconciler(
            locateGateway: { gateway },
            statusQuery: { _ in Self.status(gateway: gateway) },
            restart: { value in await recorder.restart(value) },
            fingerprintProvider: { _ in fingerprint },
            markerReader: { storedMarker },
            markerWriter: { marker in await recorder.write(marker) }
        )
    }

    private static func status(
        gateway: URL,
        installed: Bool = true,
        loaded: Bool = true
    ) -> GatewayServiceStatus {
        GatewayServiceStatus(
            installed: installed,
            loaded: loaded,
            launchConfiguration: GatewayLaunchConfiguration(
                host: "127.0.0.1",
                port: 8088,
                configPath: "/tmp/config.toml",
                executablePath: gateway.path
            ),
            health: nil
        )
    }
}
