import Foundation
import XCTest
@testable import WayfinderMacCore

final class SetupTests: XCTestCase {
    func testAssessmentCompletenessKeepsOperationalFailuresOutOfFirstRun() {
        XCTAssertTrue(SetupAssessment.neverConfigured.isIncomplete)
        XCTAssertTrue(SetupAssessment.missingKeys(["OPENAI_API_KEY"]).isIncomplete)
        XCTAssertFalse(SetupAssessment.stopped.isIncomplete)
        XCTAssertFalse(SetupAssessment.unreachableAfterSuccess.isIncomplete)
        XCTAssertFalse(SetupAssessment.healthy.isIncomplete)
    }

    func testResolverChecksInheritedAndHomebrewLocations() {
        let resolver = GatewayToolResolver(environment: ["PATH": "/custom/bin"]) { path in
            path == "/opt/homebrew/bin/wayfinder-router"
        }
        XCTAssertEqual(resolver.resolve()?.path, "/opt/homebrew/bin/wayfinder-router")
    }

    func testCommandPlanRejectsUnknownPresetAndUnsafePath() throws {
        let executable = URL(fileURLWithPath: "/bin/echo")
        XCTAssertThrowsError(try SetupCommandPlan.make(tool: executable, presetID: "unknown", configPath: GatewayServiceController.defaultConfigPath()))
        XCTAssertThrowsError(try SetupCommandPlan.make(tool: executable, presetID: "hybrid", configPath: "/tmp/router.toml"))
    }

    func testCommandPlanUsesFixedArgumentArraysWithoutForce() throws {
        let executable = URL(fileURLWithPath: "/bin/echo")
        let plan = try SetupCommandPlan.make(tool: executable, presetID: "openai", configPath: GatewayServiceController.defaultConfigPath())
        XCTAssertEqual(plan[0].arguments.prefix(4), ["init", "--preset", "openai", "--keychain"])
        XCTAssertFalse(plan.flatMap(\.arguments).contains("--force"))
        XCTAssertEqual(plan[2].arguments.prefix(2), ["service", "install"])
    }

    func testSanitizerRedactsCredentialValues() {
        XCTAssertEqual(SetupService.sanitize("failed for secret-key", secrets: ["secret-key"]), "failed for [redacted]")
    }

    @MainActor
    func testLocalPresetSkipsCredentials() {
        let state = SetupState()
        state.selectedPresetID = "local"
        XCTAssertTrue(state.requiredCredentials.isEmpty)
    }
}
