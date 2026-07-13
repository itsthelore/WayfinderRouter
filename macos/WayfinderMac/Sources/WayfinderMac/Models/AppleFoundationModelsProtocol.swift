import Foundation

#if canImport(FoundationModels)
import FoundationModels
#endif

/// Stable protocol constants shared by the native inference boundary and its callers.
public enum AppleFoundationModelsProtocolV1 {
    public static let version = 1
    public static let maximumRequestIDBytes = 128
}

/// Sanitized availability categories exposed across the XPC boundary.
public enum AppleFoundationModelsAvailability: String, Codable, CaseIterable, Sendable {
    case available
    case deviceNotEligible = "device-not-eligible"
    case appleIntelligenceNotEnabled = "apple-intelligence-not-enabled"
    case modelNotReady = "model-not-ready"
    case unsupported
    case unavailable
}

/// Framework-independent representation used to deterministically test Apple's native mapping.
public enum AppleFoundationModelsNativeAvailability: Equatable, Sendable {
    case available
    case deviceNotEligible
    case appleIntelligenceNotEnabled
    case modelNotReady
    case unknownUnavailable
}

public enum AppleFoundationModelsProtocolError: Error, Equatable, Sendable {
    case unsupportedVersion
    case invalidRequestID
}

public struct AppleFoundationModelsAvailabilityRequest: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let requestID: String

    public init(protocolVersion: Int = AppleFoundationModelsProtocolV1.version, requestID: String) {
        self.protocolVersion = protocolVersion
        self.requestID = requestID
    }

    public func validate() throws {
        guard protocolVersion == AppleFoundationModelsProtocolV1.version else {
            throw AppleFoundationModelsProtocolError.unsupportedVersion
        }
        guard !requestID.isEmpty,
              requestID.lengthOfBytes(using: .utf8)
                <= AppleFoundationModelsProtocolV1.maximumRequestIDBytes
        else {
            throw AppleFoundationModelsProtocolError.invalidRequestID
        }
    }
}

public struct AppleFoundationModelsAvailabilityResponse: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let requestID: String
    public let availability: AppleFoundationModelsAvailability

    public init(
        protocolVersion: Int = AppleFoundationModelsProtocolV1.version,
        requestID: String,
        availability: AppleFoundationModelsAvailability
    ) {
        self.protocolVersion = protocolVersion
        self.requestID = requestID
        self.availability = availability
    }
}

public enum AppleFoundationModelsAvailabilityQuery {
    /// Pure mapping kept separate from the framework query for deterministic tests.
    public static func map(
        frameworkSupported: Bool,
        native: AppleFoundationModelsNativeAvailability?
    ) -> AppleFoundationModelsAvailability {
        guard frameworkSupported, let native else { return .unsupported }
        switch native {
        case .available: return .available
        case .deviceNotEligible: return .deviceNotEligible
        case .appleIntelligenceNotEnabled: return .appleIntelligenceNotEnabled
        case .modelNotReady: return .modelNotReady
        case .unknownUnavailable: return .unavailable
        }
    }

    /// Query the actual system model only on supported SDKs and macOS versions.
    public static func current() -> AppleFoundationModelsAvailability {
        #if canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            return map(frameworkSupported: true, native: nativeAvailability())
        }
        #endif
        return .unsupported
    }

    #if canImport(FoundationModels)
    @available(macOS 26.0, *)
    private static func nativeAvailability() -> AppleFoundationModelsNativeAvailability {
        switch SystemLanguageModel.default.availability {
        case .available:
            return .available
        case .unavailable(.deviceNotEligible):
            return .deviceNotEligible
        case .unavailable(.appleIntelligenceNotEnabled):
            return .appleIntelligenceNotEnabled
        case .unavailable(.modelNotReady):
            return .modelNotReady
        @unknown default:
            return .unknownUnavailable
        }
    }
    #endif
}
