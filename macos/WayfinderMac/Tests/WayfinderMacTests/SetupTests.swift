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

    func testCommandPlanAllowsExplicitApplePresetWithoutCredentials() throws {
        let executable = URL(fileURLWithPath: "/bin/echo")
        let plan = try SetupCommandPlan.make(tool: executable, presetID: "apple-local", configPath: GatewayServiceController.defaultConfigPath())
        XCTAssertEqual(plan[0].arguments.prefix(4), ["init", "--preset", "apple-local", "--keychain"])
        XCTAssertTrue(SetupPreset.appleLocal.credentials.isEmpty)
        XCTAssertNil(SetupPreset.appleLocal.localRuntimeExecutable)
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

    func testHybridPresetRequestsTheCredentialWrittenByBootstrap() throws {
        let preset = try XCTUnwrap(SetupPreset.approved.first { $0.id == "hybrid" })
        XCTAssertEqual(
            preset.credentials,
            [SetupCredential(provider: "OpenAI", environmentVariable: "OPENAI_API_KEY")]
        )
        XCTAssertTrue(preset.requirement.contains("OpenAI"))
    }

    func testApplePresetIsOfferedOnlyWhenAvailabilityIsConfirmed() {
        XCTAssertEqual(SetupPreset.approved(appleAvailability: .available).first?.id, "apple-local")
        XCTAssertFalse(SetupPreset.approved(appleAvailability: .modelNotReady).contains(SetupPreset.appleLocal))
        XCTAssertFalse(SetupPreset.approved(appleAvailability: .deviceNotEligible).contains(SetupPreset.appleLocal))
        XCTAssertFalse(SetupPreset.approved(appleAvailability: .unsupported).contains(SetupPreset.appleLocal))
    }

    func testNewSetupSelectsAppleOnlyWhenAvailabilityIsConfirmed() {
        XCTAssertEqual(
            SetupState.selectedPresetID(afterAssessment: .neverConfigured, appleAvailability: .available, current: "hybrid"),
            "apple-local"
        )
        XCTAssertEqual(
            SetupState.selectedPresetID(afterAssessment: .neverConfigured, appleAvailability: .modelNotReady, current: "apple-local"),
            "hybrid"
        )
        XCTAssertEqual(
            SetupState.selectedPresetID(afterAssessment: .existingConfig, appleAvailability: .available, current: "openai"),
            "openai"
        )
    }

    func testAppleAvailabilityGuidancePreservesFallbackSetup() {
        XCTAssertTrue(AppleFoundationModelsAvailability.modelNotReady.setupGuidance?.contains("temporary") == true)
        XCTAssertTrue(AppleFoundationModelsAvailability.deviceNotEligible.setupGuidance?.contains("Ollama") == true)
        XCTAssertTrue(AppleFoundationModelsAvailability.unsupported.setupGuidance?.contains("Ollama") == true)
    }
}
