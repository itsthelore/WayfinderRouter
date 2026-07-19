import Foundation

public struct HelperManifest: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let implementation: String
    public let version: String
    public let targetArchitecture: String
    public let wireContractVersion: Int
    public let configSchemaMinimum: Int
    public let configSchemaMaximum: Int
    public let requiredCommands: [String]
    public let requiredNativeCommands: [String]
    public let credentialMechanisms: [String]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case implementation
        case version
        case targetArchitecture = "target_architecture"
        case wireContractVersion = "wire_contract_version"
        case configSchemaMinimum = "config_schema_minimum"
        case configSchemaMaximum = "config_schema_maximum"
        case requiredCommands = "required_commands"
        case requiredNativeCommands = "required_native_commands"
        case credentialMechanisms = "credential_mechanisms"
    }
}

public struct HelperCapabilities: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let implementation: String
    public let version: String
    public let targetArchitecture: String
    public let commands: [String]
    public let nativeCommands: [String]
    public let credentialMechanisms: [String]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case implementation
        case version
        case targetArchitecture = "target_architecture"
        case commands
        case nativeCommands = "native_commands"
        case credentialMechanisms = "credential_mechanisms"
    }
}

public enum HelperVerificationError: Error, Equatable, LocalizedError {
    case invalidManifest
    case invalidCapabilities
    case implementationMismatch
    case versionMismatch
    case architectureMismatch
    case missingCommand(String)
    case missingNativeCommand(String)
    case missingCredentialMechanism(String)

    public var errorDescription: String? {
        switch self {
        case .invalidManifest: "The bundled helper manifest is invalid."
        case .invalidCapabilities: "The bundled helper returned invalid capabilities."
        case .implementationMismatch: "The bundled helper implementation does not match its manifest."
        case .versionMismatch: "The bundled helper version does not match its manifest."
        case .architectureMismatch: "The bundled helper architecture does not match its manifest."
        case .missingCommand(let command): "The bundled helper is missing command: \(command)."
        case .missingNativeCommand(let command): "The bundled helper does not implement the required native command: \(command)."
        case .missingCredentialMechanism(let mechanism): "The bundled helper is missing credential mechanism: \(mechanism)."
        }
    }
}

public enum HelperVerifier {
    static let supportedManifestSchemaVersion = 1
    static let supportedCapabilitiesSchemaVersion = "1"
    static let supportedWireContractVersion = 1
    static let supportedConfigSchemaVersion = 1
    static let requiredCommands = ["route", "serve", "service", "capabilities"]
    static let requiredNativeCommands = [
        "route",
        "serve",
        "service",
        "capabilities",
        "app-setup-init",
        "config read-routing",
        "config apply-routing",
    ]
    static let requiredCredentialMechanisms = ["xpc-credential-broker-v1"]

    static var currentArchitecture: String {
        #if arch(arm64)
        "arm64"
        #elseif arch(x86_64)
        "x86_64"
        #else
        "unsupported"
        #endif
    }

    public static func decodeManifest(_ data: Data) throws -> HelperManifest {
        do { return try JSONDecoder().decode(HelperManifest.self, from: data) }
        catch { throw HelperVerificationError.invalidManifest }
    }

    public static func decodeCapabilities(_ data: Data) throws -> HelperCapabilities {
        do { return try JSONDecoder().decode(HelperCapabilities.self, from: data) }
        catch { throw HelperVerificationError.invalidCapabilities }
    }

    public static func verify(_ capabilities: HelperCapabilities, against manifest: HelperManifest) throws {
        guard manifest.schemaVersion == supportedManifestSchemaVersion,
              manifest.implementation == "rust",
              manifest.wireContractVersion == supportedWireContractVersion,
              manifest.configSchemaMinimum <= supportedConfigSchemaVersion,
              manifest.configSchemaMaximum >= supportedConfigSchemaVersion,
              requiredCommands.allSatisfy(manifest.requiredCommands.contains),
              requiredNativeCommands.allSatisfy(manifest.requiredNativeCommands.contains),
              requiredCredentialMechanisms.allSatisfy(manifest.credentialMechanisms.contains) else {
            throw HelperVerificationError.invalidManifest
        }
        guard capabilities.schemaVersion == supportedCapabilitiesSchemaVersion else {
            throw HelperVerificationError.invalidCapabilities
        }
        guard manifest.targetArchitecture == currentArchitecture else {
            throw HelperVerificationError.architectureMismatch
        }
        guard capabilities.implementation == manifest.implementation else {
            throw HelperVerificationError.implementationMismatch
        }
        guard capabilities.version == manifest.version else {
            throw HelperVerificationError.versionMismatch
        }
        guard capabilities.targetArchitecture == manifest.targetArchitecture else {
            throw HelperVerificationError.architectureMismatch
        }
        for command in manifest.requiredCommands where !capabilities.commands.contains(command) {
            throw HelperVerificationError.missingCommand(command)
        }
        for command in manifest.requiredNativeCommands where !capabilities.nativeCommands.contains(command) {
            throw HelperVerificationError.missingNativeCommand(command)
        }
        for mechanism in manifest.credentialMechanisms where !capabilities.credentialMechanisms.contains(mechanism) {
            throw HelperVerificationError.missingCredentialMechanism(mechanism)
        }
    }
}
