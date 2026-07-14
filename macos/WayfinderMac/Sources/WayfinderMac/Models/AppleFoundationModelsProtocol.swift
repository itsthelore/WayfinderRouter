import Foundation

#if canImport(FoundationModels)
import FoundationModels
#endif

/// Stable protocol constants shared by the native inference boundary and its callers.
public enum AppleFoundationModelsProtocolV1 {
    public static let version = 1
    public static let maximumRequestIDBytes = 128
    public static let maximumEncodedRequestBytes = 1_048_576
    public static let maximumInstructionsBytes = 16_384
    public static let maximumMessages = 64
    public static let maximumMessageBytes = 262_144
    public static let maximumAccumulatedResponseBytes = 524_288
    public static let maximumChunkBytes = 65_536
    public static let maximumQueuedChunks = 32
    public static let maximumTimeoutMilliseconds = 120_000
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
    case malformedPayload
    case requestTooLarge
    case instructionsTooLarge
    case tooManyMessages
    case invalidMessage
    case messageTooLarge
    case invalidTimeout
    case timedOut
    case responseTooLarge
    case chunkTooLarge
    case queueFull
    case unauthorizedCaller
    case cancelled
    case unavailable
}

public enum AppleFoundationModelsMessageRole: String, Codable, Sendable {
    case user
    case assistant
}

public struct AppleFoundationModelsMessage: Codable, Equatable, Sendable {
    public let role: AppleFoundationModelsMessageRole
    public let content: String

    public init(role: AppleFoundationModelsMessageRole, content: String) {
        self.role = role
        self.content = content
    }
}

public struct AppleFoundationModelsGenerateRequest: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let requestID: String
    public let instructions: String?
    public let messages: [AppleFoundationModelsMessage]
    public let timeoutMilliseconds: Int

    public init(
        protocolVersion: Int = AppleFoundationModelsProtocolV1.version,
        requestID: String,
        instructions: String? = nil,
        messages: [AppleFoundationModelsMessage],
        timeoutMilliseconds: Int
    ) {
        self.protocolVersion = protocolVersion
        self.requestID = requestID
        self.instructions = instructions
        self.messages = messages
        self.timeoutMilliseconds = timeoutMilliseconds
    }

    public func validate() throws {
        try validateEnvelope(protocolVersion: protocolVersion, requestID: requestID)
        if let instructions,
           instructions.lengthOfBytes(using: .utf8) > AppleFoundationModelsProtocolV1.maximumInstructionsBytes {
            throw AppleFoundationModelsProtocolError.instructionsTooLarge
        }
        guard !messages.isEmpty else { throw AppleFoundationModelsProtocolError.invalidMessage }
        guard messages.count <= AppleFoundationModelsProtocolV1.maximumMessages else {
            throw AppleFoundationModelsProtocolError.tooManyMessages
        }
        for message in messages {
            guard !message.content.isEmpty else { throw AppleFoundationModelsProtocolError.invalidMessage }
            guard message.content.lengthOfBytes(using: .utf8) <= AppleFoundationModelsProtocolV1.maximumMessageBytes else {
                throw AppleFoundationModelsProtocolError.messageTooLarge
            }
        }
        guard timeoutMilliseconds > 0,
              timeoutMilliseconds <= AppleFoundationModelsProtocolV1.maximumTimeoutMilliseconds else {
            throw AppleFoundationModelsProtocolError.invalidTimeout
        }
    }

    public var normalizedPrompt: String {
        messages.map { "\($0.role.rawValue):\n\($0.content)" }.joined(separator: "\n\n")
    }
}

public struct AppleFoundationModelsGenerateResponse: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let requestID: String
    public let content: String

    public init(
        protocolVersion: Int = AppleFoundationModelsProtocolV1.version,
        requestID: String,
        content: String
    ) throws {
        guard content.lengthOfBytes(using: .utf8) <= AppleFoundationModelsProtocolV1.maximumAccumulatedResponseBytes else {
            throw AppleFoundationModelsProtocolError.responseTooLarge
        }
        self.protocolVersion = protocolVersion
        self.requestID = requestID
        self.content = content
    }
}

public enum AppleFoundationModelsStreamEventKind: String, Codable, Sendable {
    case chunk
    case terminal
}

public struct AppleFoundationModelsStreamEvent: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let requestID: String
    public let sequence: Int
    public let kind: AppleFoundationModelsStreamEventKind
    public let content: String?

    public init(requestID: String, sequence: Int, kind: AppleFoundationModelsStreamEventKind, content: String? = nil) throws {
        if let content,
           content.lengthOfBytes(using: .utf8) > AppleFoundationModelsProtocolV1.maximumChunkBytes {
            throw AppleFoundationModelsProtocolError.chunkTooLarge
        }
        self.protocolVersion = AppleFoundationModelsProtocolV1.version
        self.requestID = requestID
        self.sequence = sequence
        self.kind = kind
        self.content = content
    }
}

public enum AppleFoundationModelsWireCodec {
    public static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        guard data.count <= AppleFoundationModelsProtocolV1.maximumEncodedRequestBytes else {
            throw AppleFoundationModelsProtocolError.requestTooLarge
        }
        do { return try JSONDecoder().decode(type, from: data) }
        catch { throw AppleFoundationModelsProtocolError.malformedPayload }
    }

    public static func encode<T: Encodable>(_ value: T) throws -> Data {
        try JSONEncoder().encode(value)
    }
}

public struct AppleFoundationModelsCallerIdentity: Equatable, Sendable {
    public let identifier: String
    public let teamIdentifier: String?
    public let isPlatformSigned: Bool

    public init(identifier: String, teamIdentifier: String?, isPlatformSigned: Bool) {
        self.identifier = identifier
        self.teamIdentifier = teamIdentifier
        self.isPlatformSigned = isPlatformSigned
    }
}

public struct AppleFoundationModelsCallerPolicy: Sendable {
    public static let helperIdentifier = "com.wayfinder.router.helper"
    public let expectedTeamIdentifier: String?

    public init(expectedTeamIdentifier: String? = nil) {
        self.expectedTeamIdentifier = expectedTeamIdentifier
    }

    public func authorize(_ identity: AppleFoundationModelsCallerIdentity) throws {
        guard identity.identifier == Self.helperIdentifier, identity.isPlatformSigned else {
            throw AppleFoundationModelsProtocolError.unauthorizedCaller
        }
        if let expectedTeamIdentifier,
           identity.teamIdentifier != expectedTeamIdentifier {
            throw AppleFoundationModelsProtocolError.unauthorizedCaller
        }
    }
}

public struct AppleFoundationModelsBoundedEventQueue: Sendable {
    private var events: [AppleFoundationModelsStreamEvent] = []

    public init() {}

    public mutating func append(_ event: AppleFoundationModelsStreamEvent) throws {
        guard events.count < AppleFoundationModelsProtocolV1.maximumQueuedChunks else {
            throw AppleFoundationModelsProtocolError.queueFull
        }
        events.append(event)
    }

    public mutating func drain() -> [AppleFoundationModelsStreamEvent] {
        defer { events.removeAll(keepingCapacity: true) }
        return events
    }
}

public struct AppleFoundationModelsAvailabilityRequest: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let requestID: String

    public init(protocolVersion: Int = AppleFoundationModelsProtocolV1.version, requestID: String) {
        self.protocolVersion = protocolVersion
        self.requestID = requestID
    }

    public func validate() throws {
        try validateEnvelope(protocolVersion: protocolVersion, requestID: requestID)
    }
}

private func validateEnvelope(protocolVersion: Int, requestID: String) throws {
    guard protocolVersion == AppleFoundationModelsProtocolV1.version else {
        throw AppleFoundationModelsProtocolError.unsupportedVersion
    }
    guard !requestID.isEmpty,
          requestID.lengthOfBytes(using: .utf8) <= AppleFoundationModelsProtocolV1.maximumRequestIDBytes else {
        throw AppleFoundationModelsProtocolError.invalidRequestID
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
