import Foundation
import Testing
@testable import WayfinderMacCore

struct HelperManifestTests {
    private let manifest = HelperManifest(
        schemaVersion: 1,
        implementation: "rust",
        version: "2026.7.0",
        wireContractVersion: 1,
        configSchemaMinimum: 1,
        configSchemaMaximum: 1,
        requiredCommands: ["route", "serve", "service", "capabilities"],
        credentialMechanisms: ["xpc-credential-broker-v1"]
    )

    @Test func matchingCapabilitiesVerify() throws {
        let capabilities = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "2026.7.0",
            commands: ["route", "serve", "service", "capabilities"],
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )
        try HelperVerifier.verify(capabilities, against: manifest)
    }

    @Test func versionSkewAndMissingCapabilitiesFailClosed() {
        let capabilities = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "older",
            commands: [],
            credentialMechanisms: []
        )
        #expect(throws: HelperVerificationError.versionMismatch) {
            try HelperVerifier.verify(capabilities, against: manifest)
        }
    }

    @Test func malformedDocumentsAreSanitized() {
        #expect(throws: HelperVerificationError.invalidManifest) {
            try HelperVerifier.decodeManifest(Data("secret-not-json".utf8))
        }
        #expect(throws: HelperVerificationError.invalidCapabilities) {
            try HelperVerifier.decodeCapabilities(Data("secret-not-json".utf8))
        }
    }
}
