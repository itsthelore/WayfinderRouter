import Foundation
import XCTest
@testable import WayfinderMacCore

final class HelperManifestTests: XCTestCase {
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

    func testMatchingCapabilitiesVerify() throws {
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

    func testVersionSkewAndMissingCapabilitiesFailClosed() {
        let capabilities = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "older",
            targetArchitecture: "arm64",
            commands: [],
            nativeCommands: [],
            credentialMechanisms: []
        )
        assertThrows(.versionMismatch) {
            try HelperVerifier.verify(capabilities, against: manifest)
        }
    }

    func testArchitectureMismatchFailsClosed() {
        let capabilities = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "x86_64",
            commands: ["route", "serve", "service", "capabilities"],
            nativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )

        assertThrows(.architectureMismatch) {
            try HelperVerifier.verify(capabilities, against: manifest)
        }
    }

    func testUnsupportedSchemasAndIncompleteMinimumContractFailClosed() {
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
        assertThrows(.invalidManifest) {
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
        assertThrows(.invalidManifest) {
            try HelperVerifier.verify(unsupportedCapabilities, against: incompleteManifest)
        }
    }

    func testMissingRuntimeCapabilitiesFailClosedAfterVersionAgreement() {
        let missingCommand = HelperCapabilities(
            schemaVersion: "1",
            implementation: "rust",
            version: "0.1.0",
            targetArchitecture: "arm64",
            commands: ["route", "serve", "service"],
            nativeCommands: HelperVerifier.requiredNativeCommands,
            credentialMechanisms: ["xpc-credential-broker-v1"]
        )
        assertThrows(.missingCommand("capabilities")) {
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
        assertThrows(.missingCredentialMechanism("xpc-credential-broker-v1")) {
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
        assertThrows(.missingNativeCommand("config apply-routing")) {
            try HelperVerifier.verify(missingNativeCommand, against: manifest)
        }
    }

    func testWrongWireTypesAndMissingManifestFieldsAreRejectedDuringDecode() {
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
        assertThrows(.invalidCapabilities) {
            _ = try HelperVerifier.decodeCapabilities(wrongCapabilityType)
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
        assertThrows(.invalidManifest) {
            _ = try HelperVerifier.decodeManifest(incompleteManifest)
        }
    }

    func testTargetArchitectureDecodesFromSignedManifestKey() throws {
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

        XCTAssertEqual(decoded.targetArchitecture, "arm64")
    }

    func testTargetArchitectureDecodesFromCapabilityKey() throws {
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

        XCTAssertEqual(decoded.targetArchitecture, "arm64")
        XCTAssertTrue(decoded.nativeCommands.contains("config apply-routing"))
    }

    func testMalformedDocumentsAreSanitized() {
        assertThrows(.invalidManifest) {
            _ = try HelperVerifier.decodeManifest(Data("secret-not-json".utf8))
        }
        assertThrows(.invalidCapabilities) {
            _ = try HelperVerifier.decodeCapabilities(Data("secret-not-json".utf8))
        }
    }

    private func assertThrows(
        _ expected: HelperVerificationError,
        _ expression: () throws -> Void,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(try expression(), file: file, line: line) { error in
            XCTAssertEqual(error as? HelperVerificationError, expected, file: file, line: line)
        }
    }
}
