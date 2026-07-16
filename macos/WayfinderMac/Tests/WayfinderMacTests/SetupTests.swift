import Foundation
import XCTest
@testable import WayfinderMacCore

final class SetupTests: XCTestCase {
    private actor AssessmentProbe {
        private var calls = 0
        private var continuation: CheckedContinuation<AppleFoundationModelsAvailability, Never>?

        func query() async -> AppleFoundationModelsAvailability {
            calls += 1
            return await withCheckedContinuation { continuation = $0 }
        }

        func callCount() -> Int { calls }

        func finish() {
            continuation?.resume(returning: .unsupported)
            continuation = nil
        }
    }

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

    func testBundledGatewayUsesContainingHelperAppTopology() {
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")
        XCTAssertEqual(
            GatewayToolResolver.bundledHelperURL(in: app).path,
            "/Applications/Wayfinder.app/Contents/Helpers/WayfinderGateway.app/Contents/MacOS/wayfinder-router"
        )
    }

    func testCommandPlanRejectsUnknownPresetAndUnsafePath() throws {
        let executable = URL(fileURLWithPath: "/bin/echo")
        XCTAssertThrowsError(try SetupCommandPlan.make(tool: executable, presetID: "unknown", configPath: GatewayServiceController.defaultConfigPath()))
        XCTAssertThrowsError(try SetupCommandPlan.make(tool: executable, presetID: "hybrid", configPath: "/tmp/router.toml"))
    }

    func testCommandPlanUsesFixedArgumentArraysWithoutForce() throws {
        let executable = URL(fileURLWithPath: "/bin/echo")
        let plan = try SetupCommandPlan.make(tool: executable, presetID: "openai", configPath: GatewayServiceController.defaultConfigPath())
        XCTAssertEqual(plan[0].arguments.prefix(3), ["app-setup-init", "--preset", "openai"])
        XCTAssertFalse(plan[0].arguments.contains("--keychain"))
        XCTAssertFalse(plan.flatMap(\.arguments).contains("--force"))
        XCTAssertEqual(plan[2].arguments.prefix(2), ["service", "install"])
    }

    func testCommandPlanAllowsExplicitApplePresetWithoutCredentials() throws {
        let executable = URL(fileURLWithPath: "/bin/echo")
        let plan = try SetupCommandPlan.make(tool: executable, presetID: "apple-local", configPath: GatewayServiceController.defaultConfigPath())
        XCTAssertEqual(plan[0].arguments.prefix(3), ["app-setup-init", "--preset", "apple-local"])
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

    func testAppleProductReadinessRequiresEveryComponentOnOneRealSigningTeam() {
        let identities = AppleFoundationModelsProductReadiness.requiredIdentifiers.map {
            WayfinderSignedComponentIdentity(identifier: $0, teamIdentifier: "TEAM123", isPlatformSigned: true)
        }
        XCTAssertEqual(
            AppleFoundationModelsProductReadiness.evaluate(identities),
            .ready(teamIdentifier: "TEAM123")
        )
        XCTAssertEqual(
            AppleFoundationModelsProductReadiness.evaluate(Array(identities.dropLast())),
            .incompleteOrInvalid
        )
        var wrongTeam = identities
        wrongTeam[3] = WayfinderSignedComponentIdentity(
            identifier: wrongTeam[3].identifier,
            teamIdentifier: "OTHER123",
            isPlatformSigned: true
        )
        XCTAssertEqual(AppleFoundationModelsProductReadiness.evaluate(wrongTeam), .incompleteOrInvalid)
    }

    func testSetupHidesAppleWhenProductSigningIsNotProductionReady() async {
        let service = SetupService(
            appleAvailabilityQuery: { .available },
            appleProductReadinessQuery: { false }
        )
        let availability = await service.appleFoundationModelsAvailability()
        XCTAssertEqual(availability, .unsupported)
    }

    func testSetupQueriesAppleOnlyAfterProductSigningIsReady() async {
        let service = SetupService(
            appleAvailabilityQuery: { .available },
            appleProductReadinessQuery: { true }
        )
        let availability = await service.appleFoundationModelsAvailability()
        XCTAssertEqual(availability, .available)
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

    @MainActor
    func testSetupWindowUsesCompactAndScrollableStepSizes() {
        XCTAssertEqual(SetupWindowController.preferredContentSize(for: .welcome).height, 340)
        XCTAssertEqual(SetupWindowController.preferredContentSize(for: .chooseRouting).height, 500)
        XCTAssertEqual(SetupWindowController.preferredContentSize(for: .credentials).height, 400)
        XCTAssertEqual(SetupWindowController.preferredContentSize(for: .welcome).width, 560)
    }

    @MainActor
    func testConcurrentAssessmentRequestsCollapseIntoOneTransition() async {
        let probe = AssessmentProbe()
        let missingResolver = GatewayToolResolver(environment: [:], isExecutable: { _ in false })
        let service = SetupService(
            resolver: missingResolver,
            appleAvailabilityQuery: { await probe.query() },
            appleProductReadinessQuery: { true }
        )
        let state = SetupState(service: service, resolver: missingResolver)

        let first = Task { await state.assess() }
        for _ in 0..<20 {
            if await probe.callCount() > 0 { break }
            await Task.yield()
        }
        let duplicate = Task { await state.assess() }
        await Task.yield()

        let callCount = await probe.callCount()
        XCTAssertEqual(callCount, 1)
        await probe.finish()
        await first.value
        await duplicate.value
        XCTAssertEqual(state.step, .toolsMissing)
    }
}
