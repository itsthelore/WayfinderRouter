import Foundation

public struct HelperManifest: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let implementation: String
    public let version: String
    public let wireContractVersion: Int
    public let configSchemaMinimum: Int
    public let configSchemaMaximum: Int
    public let requiredCommands: [String]
    public let credentialMechanisms: [String]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case implementation
        case version
        case wireContractVersion = "wire_contract_version"
        case configSchemaMinimum = "config_schema_minimum"
        case configSchemaMaximum = "config_schema_maximum"
        case requiredCommands = "required_commands"
        case credentialMechanisms = "credential_mechanisms"
    }
}

public struct HelperCapabilities: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let implementation: String
    public let version: String
    public let commands: [String]
    public let credentialMechanisms: [String]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case implementation
        case version
        case commands
        case credentialMechanisms = "credential_mechanisms"
    }
}

public enum HelperVerificationError: Error, Equatable, LocalizedError {
    case invalidManifest
    case invalidCapabilities
    case implementationMismatch
    case versionMismatch
    case missingCommand(String)
    case missingCredentialMechanism(String)

    public var errorDescription: String? {
        switch self {
        case .invalidManifest: "The bundled helper manifest is invalid."
        case .invalidCapabilities: "The bundled helper returned invalid capabilities."
        case .implementationMismatch: "The bundled helper implementation does not match its manifest."
        case .versionMismatch: "The bundled helper version does not match its manifest."
        case .missingCommand(let command): "The bundled helper is missing command: \(command)."
        case .missingCredentialMechanism(let mechanism): "The bundled helper is missing credential mechanism: \(mechanism)."
        }
    }
}

public enum HelperVerifier {
    public static func decodeManifest(_ data: Data) throws -> HelperManifest {
        do { return try JSONDecoder().decode(HelperManifest.self, from: data) }
        catch { throw HelperVerificationError.invalidManifest }
    }

    public static func decodeCapabilities(_ data: Data) throws -> HelperCapabilities {
        do { return try JSONDecoder().decode(HelperCapabilities.self, from: data) }
        catch { throw HelperVerificationError.invalidCapabilities }
    }

    public static func verify(_ capabilities: HelperCapabilities, against manifest: HelperManifest) throws {
        guard capabilities.implementation == manifest.implementation else {
            throw HelperVerificationError.implementationMismatch
        }
        guard capabilities.version == manifest.version else {
            throw HelperVerificationError.versionMismatch
        }
        for command in manifest.requiredCommands where !capabilities.commands.contains(command) {
            throw HelperVerificationError.missingCommand(command)
        }
        for mechanism in manifest.credentialMechanisms where !capabilities.credentialMechanisms.contains(mechanism) {
            throw HelperVerificationError.missingCredentialMechanism(mechanism)
        }
    }
}
