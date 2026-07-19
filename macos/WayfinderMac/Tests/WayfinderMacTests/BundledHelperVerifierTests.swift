import Foundation
import XCTest
@testable import WayfinderMacCore

final class BundledHelperVerifierTests: XCTestCase {
    func testIntegrityFailureStopsBeforeManifestReadOrHelperExecution() async {
        let trace = VerificationTrace()
        let verifier = BundledHelperVerifier(
            isExecutable: { _ in true },
            integrityChecker: { _ in trace.append("integrity"); return false },
            dataLoader: { _, _ in trace.append("manifest"); return Data() },
            versionLoader: { _ in trace.append("version"); return "0.1.0" },
            capabilitiesRunner: { _ in trace.append("capabilities"); return Data() }
        )
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")

        do {
            _ = try await verifier.verify(
                appBundleURL: app,
                helperURL: GatewayToolResolver.bundledHelperURL(in: app)
            )
            XCTFail("Expected integrity failure")
        } catch {
            XCTAssertEqual(error as? BundledHelperVerificationError, .invalidCodeIntegrity)
        }
        XCTAssertEqual(trace.values, ["integrity"])
    }

    func testMatchingManifestAndCapabilitiesAreCheckedBetweenTwoIntegrityPasses() async throws {
        let trace = VerificationTrace()
        let verifier = makeVerifier(trace: trace)
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")
        let helper = GatewayToolResolver.bundledHelperURL(in: app)

        let resolved = try await verifier.verify(appBundleURL: app, helperURL: helper)
        XCTAssertEqual(resolved, helper)
        XCTAssertEqual(trace.values, ["integrity", "manifest", "version", "capabilities", "integrity"])
    }

    func testDesktopVersionMismatchStopsBeforeHelperExecution() async {
        let trace = VerificationTrace()
        let verifier = makeVerifier(trace: trace, desktopVersion: "0.2.0")
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")

        do {
            _ = try await verifier.verify(
                appBundleURL: app,
                helperURL: GatewayToolResolver.bundledHelperURL(in: app)
            )
            XCTFail("Expected version mismatch")
        } catch {
            XCTAssertEqual(error as? BundledHelperVerificationError, .desktopVersionMismatch)
        }
        XCTAssertEqual(trace.values, ["integrity", "manifest", "version"])
    }

    func testInvalidCapabilitiesFailClosed() async {
        let trace = VerificationTrace()
        let verifier = makeVerifier(trace: trace, capabilities: Data("not-json".utf8))
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")

        do {
            _ = try await verifier.verify(
                appBundleURL: app,
                helperURL: GatewayToolResolver.bundledHelperURL(in: app)
            )
            XCTFail("Expected capability failure")
        } catch {
            XCTAssertEqual(error as? BundledHelperVerificationError, .capabilitiesUnavailable)
        }
        XCTAssertEqual(trace.values, ["integrity", "manifest", "version", "capabilities"])
    }

    func testOversizedCapabilitiesFailClosedBeforeDecode() async {
        let trace = VerificationTrace()
        let oversized = Data(
            repeating: 0x20,
            count: BundledHelperVerifier.maximumCapabilitiesBytes + 1
        )
        let verifier = makeVerifier(trace: trace, capabilities: oversized)
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")

        do {
            _ = try await verifier.verify(
                appBundleURL: app,
                helperURL: GatewayToolResolver.bundledHelperURL(in: app)
            )
            XCTFail("Expected capability size failure")
        } catch {
            XCTAssertEqual(error as? BundledHelperVerificationError, .capabilitiesUnavailable)
        }
        XCTAssertEqual(trace.values, ["integrity", "manifest", "version", "capabilities"])
    }

    func testManifestLoaderRejectsOversizedAndSymbolicLinkFiles() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let oversized = directory.appendingPathComponent("oversized.json")
        try Data(repeating: 0x20, count: 65).write(to: oversized)
        XCTAssertThrowsError(
            try BundledHelperVerifier.loadBoundedRegularFile(oversized, maximumBytes: 64)
        )

        let target = directory.appendingPathComponent("target.json")
        try Data("{}".utf8).write(to: target)
        let link = directory.appendingPathComponent("manifest.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        XCTAssertThrowsError(
            try BundledHelperVerifier.loadBoundedRegularFile(link, maximumBytes: 64)
        )
    }

    func testRoutingCommandFailsClosedWithoutRunningPATHGateway() async {
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")
        let resolver = GatewayToolResolver(
            environment: ["PATH": "/attacker/bin"],
            appBundleURL: app,
            isExecutable: { $0 == "/attacker/bin/wayfinder-router" },
            bundledGatewayVerifier: { _, _ in XCTFail("Missing bundle must fail before verification") }
        )

        let result = await RoutingConfigStore.runWayfinderRouter(
            resolver: resolver,
            arguments: ["config", "read-routing", "--path", "/tmp/config.toml"],
            stdin: nil
        )

        XCTAssertFalse(result.isSuccess)
        XCTAssertTrue(result.stderr.contains("Reinstall Wayfinder"))
    }

    func testRuntimeGateRejectsExternalLaunchAgentAfterBundledVerification() async {
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")
        let helper = GatewayToolResolver.bundledHelperURL(in: app)
        let resolver = GatewayToolResolver(
            environment: [:],
            appBundleURL: app,
            isExecutable: { $0 == helper.path },
            bundledGatewayVerifier: { _, _ in }
        )
        let runtime = VerifiedGatewayRuntime(
            resolver: resolver,
            launchConfigurationQuery: {
                GatewayLaunchConfiguration(
                    host: "127.0.0.1",
                    port: 8088,
                    configPath: "/tmp/config.toml",
                    executablePath: "/opt/homebrew/bin/wayfinder-router"
                )
            }
        )

        do {
            try await runtime.validate()
            XCTFail("Expected external service rejection")
        } catch {
            XCTAssertEqual(error as? VerifiedGatewayRuntimeError, .serviceNeedsRepair)
        }
    }

    func testRuntimeGateAcceptsExactVerifiedBundledLaunchAgent() async throws {
        let app = URL(fileURLWithPath: "/Applications/Wayfinder.app")
        let helper = GatewayToolResolver.bundledHelperURL(in: app)
        let resolver = GatewayToolResolver(
            environment: [:],
            appBundleURL: app,
            isExecutable: { $0 == helper.path },
            bundledGatewayVerifier: { _, _ in }
        )
        let runtime = VerifiedGatewayRuntime(
            resolver: resolver,
            launchConfigurationQuery: {
                GatewayLaunchConfiguration(
                    host: "127.0.0.1",
                    port: 8088,
                    configPath: "/tmp/config.toml",
                    executablePath: helper.path
                )
            }
        )

        try await runtime.validate()
    }

    func testBoundedRunnerTerminatesAStalledProcess() async {
        let started = ContinuousClock.now
        do {
            _ = try await BoundedProcessRunner.run(
                executable: URL(fileURLWithPath: "/bin/sleep"),
                arguments: ["5"],
                timeoutNanoseconds: 75_000_000,
                maximumOutputBytes: 1_024
            )
            XCTFail("Expected timeout")
        } catch {
            XCTAssertEqual(error as? BoundedProcessError, .timedOut)
        }
        XCTAssertLessThan(started.duration(to: .now), .seconds(2))
    }

    func testBoundedRunnerTerminatesExcessiveOutput() async {
        do {
            _ = try await BoundedProcessRunner.run(
                executable: URL(fileURLWithPath: "/usr/bin/yes"),
                arguments: [],
                timeoutNanoseconds: 2_000_000_000,
                maximumOutputBytes: 1_024
            )
            XCTFail("Expected output limit")
        } catch {
            XCTAssertEqual(error as? BoundedProcessError, .outputLimitExceeded)
        }
    }

    private func makeVerifier(
        trace: VerificationTrace,
        desktopVersion: String = "0.1.0",
        capabilities: Data? = nil
    ) -> BundledHelperVerifier {
        let architecture = HelperVerifier.currentArchitecture
        let manifest = Data(
            """
            {
              "schema_version": 1,
              "implementation": "rust",
              "version": "0.1.0",
              "target_architecture": "\(architecture)",
              "wire_contract_version": 1,
              "config_schema_minimum": 1,
              "config_schema_maximum": 1,
              "required_commands": ["route", "serve", "service", "capabilities"],
              "required_native_commands": ["route", "serve", "service", "capabilities", "app-setup-init", "config read-routing", "config apply-routing"],
              "credential_mechanisms": ["xpc-credential-broker-v1"]
            }
            """.utf8
        )
        let matchingCapabilities = Data(
            """
            {
              "schema_version": "1",
              "implementation": "rust",
              "version": "0.1.0",
              "target_architecture": "\(architecture)",
              "commands": ["route", "serve", "service", "capabilities"],
              "native_commands": ["route", "serve", "service", "capabilities", "app-setup-init", "config read-routing", "config apply-routing"],
              "credential_mechanisms": ["xpc-credential-broker-v1"]
            }
            """.utf8
        )
        return BundledHelperVerifier(
            isExecutable: { _ in true },
            integrityChecker: { _ in trace.append("integrity"); return true },
            dataLoader: { _, _ in trace.append("manifest"); return manifest },
            versionLoader: { _ in trace.append("version"); return desktopVersion },
            capabilitiesRunner: { _ in
                trace.append("capabilities")
                return capabilities ?? matchingCapabilities
            }
        )
    }
}

private final class VerificationTrace: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: [String] = []

    var values: [String] {
        lock.withLock { stored }
    }

    func append(_ value: String) {
        lock.withLock { stored.append(value) }
    }
}
