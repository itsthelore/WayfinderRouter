import Foundation
import Testing
@testable import WayfinderMacCore

struct HelperManifestTests {
    private let manifest = HelperManifest(
        schemaVersion: 1,
        implementation: "rust",
        version: "0.1.0",
        targetArchitecture: "arm64",
        wireContractVersion: 1,
        configSchemaMinimum: 1,
        configSchemaMaximum: 1,
        requiredCommands: ["route", "serve", "service", "capabilities"],
        requiredNativeCommands: HelperVerifier.requiredNativeCommands,
        credentialMechanisms: ["xpc-credential-broker-v1"]
    )

    @Test func matchingCapabilitiesVerify() throws {
        let capabilities = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            commands: ["route", "serve", "service", "capabilities"],
            nativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )
        try HelperVerifier.verify(capabilities, against: manifest)
    }

    @Test func versionSkewAndMissingCapabilitiesFailClosed() {
        let capabilities = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "older",
            targetArchitecture: "arm64",
            commands: [],
            nativeCommands: [],
            credentialMechanisms: []
        )
        #expect(throws: HelperVerificationError.versionMismatch) {
            try HelperVerifier.verify(capabilities, against: manifest)
        }
    }

    @Test func architectureMismatchFailsClosed() {
        let capabilities = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "x86_64",
            commands: ["route", "serve", "service", "capabilities"],
            nativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )

        #expect(throws: HelperVerificationError.architectureMismatch) {
            try HelperVerifier.verify(capabilities, against: manifest)
        }
    }

    @Test func unsupportedSchemasAndIncompleteMinimumContractFailClosed() {
        let unsupportedManifest = HelperManifest(
            schemaVersion: 2,
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            wireContractVersion: 1,
            configSchemaMinimum: 1,
            configSchemaMaximum: 1,
            requiredCommands: ["route", "serve", "service", "capabilities"],
            requiredNativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )
        let unsupportedCapabilities = HelperCapabilities(
            schemaVersion: "2",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            commands: ["route", "serve", "service", "capabilities"],
            nativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )
        #expect(throws: HelperVerificationError.invalidManifest) {
            try HelperVerifier.verify(unsupportedCapabilities, against: unsupportedManifest)
        }

        let incompleteManifest = HelperManifest(
            schemaVersion: 1,
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            wireContractVersion: 1,
            configSchemaMinimum: 1,
            configSchemaMaximum: 1,
            requiredCommands: ["route"],
            requiredNativeCommands: [],
            credentialMechanisms: []
        )
        #expect(throws: HelperVerificationError.invalidManifest) {
            try HelperVerifier.verify(unsupportedCapabilities, against: incompleteManifest)
        }
    }

    @Test func missingRuntimeCapabilitiesFailClosedAfterVersionAgreement() {
        let missingCommand = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            commands: ["route", "serve", "service"],
            nativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )
        #expect(throws: HelperVerificationError.missingCommand("capabilities")) {
            try HelperVerifier.verify(missingCommand, against: manifest)
        }

        let missingCredentialMechanism = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            commands: ["route", "serve", "service", "capabilities"],
            nativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: []
        )
        #expect(throws: HelperVerificationError.missingCredentialMechanism("xpc-credential-broker-v1")) {
            try HelperVerifier.verify(missingCredentialMechanism, against: manifest)
        }

        let missingNativeCommand = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            commands: ["route", "serve", "service", "capabilities"],
            nativeCommands: HelperVerifier.requiredNativeCommands.filter { $0 != "config apply-routing" },
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )
        #expect(throws: HelperVerificationError.missingNativeCommand("config apply-routing")) {
            try HelperVerifier.verify(missingNativeCommand, against: manifest)
        }
    }

    @Test func wrongWireTypesAndMissingManifestFieldsAreRejectedDuringDecode() {
        let wrongCapabilityType = Data(
            #"""
            {
              "schema_version": 1,
              "implementation": "rust",
              "version": "0.1.0",
              "target_architecture": "arm64",
              "commands": [],
              "native_commands": [],
              "credential_mechanisms": []
            }
            """#.utf8
        )
        #expect(throws: HelperVerificationError.invalidCapabilities) {
            try HelperVerifier.decodeCapabilities(wrongCapabilityType)
        }

        let incompleteManifest = Data(
            #"""
            {
              "schema_version": 1,
              "implementation": "rust",
              "version": "0.1.0",
              "target_architecture": "arm64",
              "required_commands": [],
              "credential_mechanisms": []
            }
            """#.utf8
        )
        #expect(throws: HelperVerificationError.invalidManifest) {
            try HelperVerifier.decodeManifest(incompleteManifest)
        }
    }

    @Test func targetArchitectureDecodesFromSignedManifestKey() throws {
        let data = Data(
            #"""
            {
              "schema_version": 1,
              "implementation": "rust",
              "version": "0.1.0",
              "target_architecture": "arm64",
              "wire_contract_version": 1,
              "config_schema_minimum": 1,
              "config_schema_maximum": 1,
              "required_commands": ["route", "serve", "service", "capabilities"],
              "required_native_commands": ["route", "serve", "service", "capabilities", "app-setup-init", "config read-routing", "config apply-routing"],
              "credential_mechanisms": ["xpc-credential-broker-v1"]
            }
            """#.utf8
        )

        let decoded = try HelperVerifier.decodeManifest(data)

        #expect(decoded.targetArchitecture == "arm64")
    }

    @Test func targetArchitectureDecodesFromCapabilityKey() throws {
        let data = Data(
            #"""
            {
              "schema_version": "1",
              "implementation": "rust",
              "version": "0.1.0",
              "target_architecture": "arm64",
              "commands": ["route", "serve", "service", "capabilities"],
              "native_commands": ["route", "serve", "service", "capabilities", "app-setup-init", "config read-routing", "config apply-routing"],
              "credential_mechanisms": ["xpc-credential-broker-v1"]
            }
            """#.utf8
        )

        let decoded = try HelperVerifier.decodeCapabilities(data)

        #expect(decoded.targetArchitecture == "arm64")
        #expect(decoded.nativeCommands.contains("config apply-routing"))
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
