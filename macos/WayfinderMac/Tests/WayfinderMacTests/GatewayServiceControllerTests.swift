import Foundation
import XCTest
@testable import WayfinderMacCore

final class GatewayServiceControllerTests: XCTestCase {
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
}
