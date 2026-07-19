import Foundation
import XCTest
@testable import WayfinderMacCore

final class GatewayServiceControllerTests: XCTestCase {
    private actor HealthProbeCounter {
        private var urls: [URL] = []

        func record(_ url: URL) {
            urls.append(url)
        }

        func recordedURLs() -> [URL] { urls }
    }

    func testExtractsHostPortAndConfigFromProgramArguments() {
        let config = GatewayServiceController.launchConfiguration(fromProgramArguments: [
            "/usr/local/bin/wayfinder-router",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "9191",
            "--config",
            "/Users/test/wayfinder-router.toml",
        ])

        XCTAssertEqual(config.host, "127.0.0.1")
        XCTAssertEqual(config.port, 9191)
        XCTAssertEqual(config.configPath, "/Users/test/wayfinder-router.toml")
        XCTAssertEqual(config.executablePath, "/usr/local/bin/wayfinder-router")
        XCTAssertTrue(config.usesGateway(at: URL(fileURLWithPath: "/usr/local/bin/wayfinder-router")))
        XCTAssertFalse(config.usesGateway(at: URL(fileURLWithPath: "/Applications/Wayfinder.app/Contents/Helpers/wayfinder-router")))
    }

    func testProgramArgumentsFallbackToGatewayDefaults() {
        let config = GatewayServiceController.launchConfiguration(fromProgramArguments: [
            "/usr/local/bin/wayfinder-router",
            "serve",
        ])

        XCTAssertEqual(config.host, "127.0.0.1")
        XCTAssertEqual(config.port, 8088)
        XCTAssertEqual(config.configPath, GatewayServiceController.defaultConfigPath())
    }

    func testEndpointFormatting() {
        let config = GatewayLaunchConfiguration(
            host: "127.0.0.1",
            port: 8088,
            configPath: "/tmp/wayfinder-router.toml"
        )

        XCTAssertEqual(config.localRouterURL, "http://127.0.0.1:8088")
        XCTAssertEqual(config.openAIBaseURL, "http://127.0.0.1:8088/v1")
        XCTAssertEqual(config.anthropicRootURL, "http://127.0.0.1:8088")
        XCTAssertEqual(config.healthURLString, "http://127.0.0.1:8088/healthz")
    }

    func testWildcardBindUsesLoopbackForPasteableEndpoint() {
        let config = GatewayLaunchConfiguration(
            host: "0.0.0.0",
            port: 8088,
            configPath: "/tmp/wayfinder-router.toml"
        )

        XCTAssertEqual(config.localRouterURL, "http://127.0.0.1:8088")
        XCTAssertEqual(config.openAIBaseURL, "http://127.0.0.1:8088/v1")
        XCTAssertEqual(config.anthropicRootURL, "http://127.0.0.1:8088")
        XCTAssertEqual(config.bindDescription, "Network-exposed bind: 0.0.0.0:8088")
    }

    func testIPv6WildcardBindUsesLoopbackForPasteableEndpoint() {
        let config = GatewayLaunchConfiguration(
            host: "::",
            port: 8088,
            configPath: "/tmp/wayfinder-router.toml"
        )

        XCTAssertEqual(config.localRouterURL, "http://127.0.0.1:8088")
        XCTAssertEqual(config.openAIBaseURL, "http://127.0.0.1:8088/v1")
        XCTAssertEqual(config.anthropicRootURL, "http://127.0.0.1:8088")
        XCTAssertEqual(config.bindDescription, "Network-exposed bind: :::8088")
    }

    func testIPv6LoopbackFormatsAsAValidBracketedURL() {
        let config = GatewayLaunchConfiguration(
            host: "::1",
            port: 8088,
            configPath: "/tmp/wayfinder-router.toml"
        )

        XCTAssertEqual(config.localRouterURL, "http://[::1]:8088")
        XCTAssertEqual(config.openAIBaseURL, "http://[::1]:8088/v1")
        XCTAssertEqual(config.healthURLString, "http://[::1]:8088/healthz")
        XCTAssertNotNil(config.healthURL)
    }

    func testBracketedIPv6HostIsNormalizedBeforeURLConstruction() {
        let config = GatewayLaunchConfiguration(
            host: "[::1]",
            port: 8088,
            configPath: "/tmp/wayfinder-router.toml"
        )

        XCTAssertEqual(config.localRouterURL, "http://[::1]:8088")
    }

    func testInvalidLaunchAgentPortFallsBackToDefault() {
        let config = GatewayServiceController.launchConfiguration(fromProgramArguments: [
            "/usr/local/bin/wayfinder-router",
            "serve",
            "--port",
            "70000",
        ])

        XCTAssertEqual(config.port, GatewayServiceController.defaultPort)
    }

    func testStatusProbesOnlyTheExpectedBundledGatewayOnLiteralLoopback() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let launchAgent = directory.appendingPathComponent("com.wayfinder-router.gateway.plist")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let bundled = URL(fileURLWithPath: "/Applications/Wayfinder.app/Contents/Helpers/WayfinderGateway.app/Contents/MacOS/wayfinder-router")
        try writeLaunchAgent(at: launchAgent, executable: bundled.path, host: "127.0.0.1")
        let probes = HealthProbeCounter()
        let controller = GatewayServiceController(launchAgentURL: launchAgent) { url in
            await probes.record(url)
            return GatewayHealth(status: "ok", models: ["local"], offline: false)
        }

        let verified = await controller.status(expectedGateway: bundled)
        XCTAssertEqual(verified.health?.status, "ok")
        var recordedURLs = await probes.recordedURLs()
        XCTAssertEqual(recordedURLs.count, 1)

        let legacy = await controller.status(
            expectedGateway: URL(fileURLWithPath: "/opt/homebrew/bin/wayfinder-router")
        )
        XCTAssertNil(legacy.health)
        recordedURLs = await probes.recordedURLs()
        XCTAssertEqual(recordedURLs.count, 1)
    }

    func testStatusAndRestartFailClosedForRemoteOrMismatchedLaunchAgent() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let launchAgent = directory.appendingPathComponent("com.wayfinder-router.gateway.plist")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let bundled = URL(fileURLWithPath: "/Applications/Wayfinder.app/Contents/Helpers/WayfinderGateway.app/Contents/MacOS/wayfinder-router")
        try writeLaunchAgent(at: launchAgent, executable: bundled.path, host: "gateway.example.com")
        let probes = HealthProbeCounter()
        let controller = GatewayServiceController(launchAgentURL: launchAgent) { url in
            await probes.record(url)
            return GatewayHealth(status: "ok", models: ["local"], offline: false)
        }

        let remoteStatus = await controller.status(expectedGateway: bundled)
        XCTAssertNil(remoteStatus.health)
        let recordedURLs = await probes.recordedURLs()
        XCTAssertTrue(recordedURLs.isEmpty)

        do {
            try await controller.restart(
                expectedGateway: URL(fileURLWithPath: "/opt/homebrew/bin/wayfinder-router")
            )
            XCTFail("A mismatched LaunchAgent must not be restarted")
        } catch {
            guard case GatewayServiceControllerError.serviceNeedsRepair = error else {
                return XCTFail("Unexpected error: \(error)")
            }
        }
    }

    func testHealthResponseDecodingOk() throws {
        let health = try decode("""
        {
          "status": "ok",
          "models": ["cloud", "local"],
          "offline": false
        }
        """)

        XCTAssertEqual(health.status, "ok")
        XCTAssertEqual(health.models, ["cloud", "local"])
        XCTAssertFalse(health.offline)
        XCTAssertEqual(health.displayStatus, "Healthy")
    }

    func testAvailableRouteNamesIncludeBuiltInsBeforeConfiguredModels() throws {
        let health = try decode("""
        {
          "status": "ok",
          "models": ["local", "cloud"],
          "offline": false
        }
        """)

        XCTAssertEqual(
            health.availableRouteNames,
            ["auto", "prefer-local", "prefer-hosted", "local", "cloud"]
        )
    }

    func testAvailableRouteNamesDeduplicateBuiltInsFromConfiguredModels() throws {
        let health = try decode("""
        {
          "status": "ok",
          "models": ["auto", "local", "prefer-hosted"],
          "offline": false
        }
        """)

        XCTAssertEqual(
            health.availableRouteNames,
            ["auto", "prefer-local", "prefer-hosted", "local"]
        )
    }

    func testHealthResponseDecodingDegradedAndMissingKeys() throws {
        let health = try decode("""
        {
          "status": "degraded",
          "models": ["cloud", "local"],
          "offline": false,
          "missing_keys": ["cloud"]
        }
        """)

        XCTAssertEqual(health.status, "degraded")
        XCTAssertEqual(health.missingKeys, ["cloud"])
        XCTAssertEqual(health.displayStatus, "Degraded")
        XCTAssertTrue(health.detailSummary.contains("missing keys: cloud"))
    }

    func testHealthResponseDecodingOffline() throws {
        let health = try decode("""
        {
          "status": "ok",
          "models": ["local"],
          "offline": true
        }
        """)

        XCTAssertTrue(health.offline)
        XCTAssertEqual(health.displayStatus, "Offline")
        XCTAssertTrue(health.detailSummary.contains("offline mode"))
    }

    private func decode(_ json: String) throws -> GatewayHealth {
        try GatewayServiceController.decodeHealth(Data(json.utf8))
    }

    private func writeLaunchAgent(
        at url: URL,
        executable: String,
        host: String
    ) throws {
        let plist: [String: Any] = [
            "Label": GatewayServiceController.launchdLabel,
            "ProgramArguments": [
                executable,
                "serve",
                "--host",
                host,
                "--port",
                "8088",
                "--config",
                GatewayServiceController.defaultConfigPath(),
            ],
        ]
        let data = try PropertyListSerialization.data(
            fromPropertyList: plist,
            format: .xml,
            options: 0
        )
        try data.write(to: url, options: .atomic)
    }
}
