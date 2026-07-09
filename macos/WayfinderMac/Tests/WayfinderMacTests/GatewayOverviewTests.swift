import Foundation
import XCTest
@testable import WayfinderMacCore

final class GatewayOverviewTests: XCTestCase {
    func testGatewayStateMappingHealthy() {
        let state = GatewayWayfinderClient.gatewayDisplayState(from: serviceStatus(
            health: GatewayHealth(status: "ok", models: ["local", "cloud"], offline: false)
        ))

        XCTAssertEqual(state.title, "Running")
        XCTAssertEqual(state.detail, "2 configured models")
        XCTAssertTrue(state.isRunning)
    }

    func testGatewayStateMappingDegradedMissingKeys() {
        let state = GatewayWayfinderClient.gatewayDisplayState(from: serviceStatus(
            health: GatewayHealth(status: "degraded", models: ["local", "cloud"], offline: false, missingKeys: ["cloud"])
        ))

        XCTAssertEqual(state.title, "Degraded")
        XCTAssertEqual(state.detail, "Missing cloud")
        XCTAssertTrue(state.isRunning)
    }

    func testGatewayStateMappingOffline() {
        let state = GatewayWayfinderClient.gatewayDisplayState(from: serviceStatus(
            health: GatewayHealth(status: "ok", models: ["local"], offline: true)
        ))

        XCTAssertEqual(state.title, "Offline")
        XCTAssertEqual(state.detail, "Offline mode keeps delivery local")
        XCTAssertTrue(state.isRunning)
    }

    func testGatewayStateMappingStoppedUnreachableAndNotInstalled() {
        XCTAssertEqual(
            GatewayWayfinderClient.gatewayDisplayState(from: serviceStatus(loaded: false)).title,
            "Stopped"
        )
        XCTAssertEqual(
            GatewayWayfinderClient.gatewayDisplayState(from: serviceStatus(health: nil)).title,
            "Unreachable"
        )
        XCTAssertEqual(
            GatewayWayfinderClient.gatewayDisplayState(from: serviceStatus(installed: false)).title,
            "Not Installed"
        )
    }

    func testHostedStateMappingNoModelsAndMissingKeys() {
        let noModels = GatewayWayfinderClient.hostedDisplayState(
            gateway: .running(detail: "ready"),
            health: GatewayHealth(status: "ok", models: [], offline: false),
            models: []
        )
        XCTAssertEqual(noModels.title, "No Models")

        let checkKeys = GatewayWayfinderClient.hostedDisplayState(
            gateway: .degraded(detail: "missing"),
            health: GatewayHealth(status: "degraded", models: ["local", "cloud"], offline: false, missingKeys: ["cloud"]),
            models: [
                GatewayModelInfo(name: "local", endpoint: "http://localhost", model: "llama", apiKeyEnv: nil, keyOK: true),
                GatewayModelInfo(name: "cloud", endpoint: "https://example.com", model: "big", apiKeyEnv: "CLOUD_KEY", keyOK: false),
            ]
        )
        XCTAssertEqual(checkKeys.title, "Check Keys")
        XCTAssertEqual(checkKeys.detail, "CLOUD_KEY")
    }

    func testHostedStateMappingDisabledUnavailableAndReady() {
        XCTAssertEqual(
            GatewayWayfinderClient.hostedDisplayState(gateway: .offline(detail: "off"), health: nil, models: []).title,
            "Disabled"
        )
        XCTAssertEqual(
            GatewayWayfinderClient.hostedDisplayState(gateway: .unreachable(detail: "no"), health: nil, models: []).title,
            "Unavailable"
        )

        let ready = GatewayWayfinderClient.hostedDisplayState(
            gateway: .running(detail: "ready"),
            health: GatewayHealth(status: "ok", models: ["local"], offline: false),
            models: [GatewayModelInfo(name: "local", endpoint: "http://localhost", model: "llama", apiKeyEnv: nil, keyOK: true)]
        )
        XCTAssertEqual(ready.title, "Ready")
    }

    func testRoutingCountsUseCheapestConfiguredModelAsLocalNumerator() {
        let stats = GatewayWayfinderClient.routingStats(
            gateway: .running(detail: "ready"),
            models: [
                GatewayModelInfo(name: "local", endpoint: "http://localhost", model: "llama", apiKeyEnv: nil, keyOK: true),
                GatewayModelInfo(name: "cloud", endpoint: "https://example.com", model: "big", apiKeyEnv: "CLOUD_KEY", keyOK: true),
            ],
            recent: GatewayRecentResponse(total: 5, byModel: ["local": 3, "cloud": 2], recent: []),
            savingsToday: nil,
            savingsThirtyDays: nil,
            updatedAt: Date()
        )

        XCTAssertEqual(stats.localRouteCount, 3)
        XCTAssertEqual(stats.cloudRouteCount, 2)
        XCTAssertEqual(stats.totalTurns, 5)
        XCTAssertEqual(stats.localPercent, 0.6)
        XCTAssertEqual(stats.cloudPercent, 0.4)
    }

    func testSavingsDisplayHidesZeroAndUnpricedValues() {
        let zero = decodeSavings(#"{"saved":0,"saved_pct":0,"priced":true,"requests":3,"baseline":0}"#)
        XCTAssertEqual(zero.displayLine(period: "Today"), "Today: Not yet available")

        let unpriced = decodeSavings(#"{"saved":10,"saved_pct":20,"priced":false,"requests":3,"baseline":50}"#)
        XCTAssertEqual(unpriced.displayLine(period: "Today"), "Today: Not yet available")

        let priced = decodeSavings(#"{"saved":0.01,"saved_pct":29,"priced":true,"requests":1,"baseline":0.02}"#)
        XCTAssertEqual(priced.displayLine(period: "Today"), "Today: $0.01 · 29% vs always-cloud")
    }

    private func serviceStatus(
        installed: Bool = true,
        loaded: Bool = true,
        health: GatewayHealth? = GatewayHealth(status: "ok", models: ["local"], offline: false)
    ) -> GatewayServiceStatus {
        GatewayServiceStatus(
            installed: installed,
            loaded: loaded,
            launchConfiguration: GatewayLaunchConfiguration(
                host: "127.0.0.1",
                port: 8088,
                configPath: "/tmp/wayfinder-router.toml"
            ),
            health: health
        )
    }

    private func decodeSavings(_ json: String) -> GatewaySavingsResponse {
        try! JSONDecoder().decode(GatewaySavingsResponse.self, from: Data(json.utf8))
    }
}
