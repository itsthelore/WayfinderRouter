import Foundation
import Testing
@testable import WayfinderMacCore

struct AppleFoundationModelsProtocolTests {
    @Test func availabilityCategoriesMapWithoutInferringReadiness() {
        let cases: [(AppleFoundationModelsNativeAvailability, AppleFoundationModelsAvailability)] = [
            (.available, .available),
            (.deviceNotEligible, .deviceNotEligible),
            (.appleIntelligenceNotEnabled, .appleIntelligenceNotEnabled),
            (.modelNotReady, .modelNotReady),
            (.unknownUnavailable, .unavailable),
        ]
        for (native, expected) in cases {
            #expect(
                AppleFoundationModelsAvailabilityQuery.map(
                    frameworkSupported: true,
                    native: native
                ) == expected
            )
        }
    }

    @Test func olderOSOrMissingFrameworkIsUnsupported() {
        #expect(
            AppleFoundationModelsAvailabilityQuery.map(
                frameworkSupported: false,
                native: .available
            ) == .unsupported
        )
        #expect(
            AppleFoundationModelsAvailabilityQuery.map(
                frameworkSupported: true,
                native: nil
            ) == .unsupported
        )
    }

    @Test func requestVersionAndIdentifierAreBounded() throws {
        try AppleFoundationModelsAvailabilityRequest(requestID: "opaque-request").validate()
        #expect(throws: AppleFoundationModelsProtocolError.unsupportedVersion) {
            try AppleFoundationModelsAvailabilityRequest(
                protocolVersion: 2,
                requestID: "opaque-request"
            ).validate()
        }
        for requestID in ["", String(repeating: "x", count: 129)] {
            #expect(throws: AppleFoundationModelsProtocolError.invalidRequestID) {
                try AppleFoundationModelsAvailabilityRequest(requestID: requestID).validate()
            }
        }
    }

    @Test func availabilityWireValuesAreStableAndRoundTrip() throws {
        let response = AppleFoundationModelsAvailabilityResponse(
            requestID: "opaque-request",
            availability: .modelNotReady
        )
        let data = try JSONEncoder().encode(response)
        let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(object["protocolVersion"] as? Int == 1)
        #expect(object["requestID"] as? String == "opaque-request")
        #expect(object["availability"] as? String == "model-not-ready")
        #expect(
            try JSONDecoder().decode(
                AppleFoundationModelsAvailabilityResponse.self,
                from: data
            ) == response
        )
    }

    @Test func protocolErrorsDoNotEchoRequestContent() {
        let marker = "prompt-or-secret-marker"
        let rendered = String(describing: AppleFoundationModelsProtocolError.invalidRequestID)
        #expect(!rendered.contains(marker))
    }

    @Test func malformedAndOversizedPayloadsAreRejectedDeterministically() {
        #expect(throws: AppleFoundationModelsProtocolError.malformedPayload) {
            try AppleFoundationModelsWireCodec.decode(
                AppleFoundationModelsGenerateRequest.self,
                from: Data("not-json".utf8)
            )
        }
        #expect(throws: AppleFoundationModelsProtocolError.requestTooLarge) {
            try AppleFoundationModelsWireCodec.decode(
                AppleFoundationModelsGenerateRequest.self,
                from: Data(repeating: 0, count: AppleFoundationModelsProtocolV1.maximumEncodedRequestBytes + 1)
            )
        }
    }

    @Test func generationRequestEnforcesContentCountAndDeadlineBounds() throws {
        let valid = AppleFoundationModelsGenerateRequest(
            requestID: "request-1",
            instructions: "Answer briefly.",
            messages: [.init(role: .user, content: "Hello")],
            timeoutMilliseconds: 1_000
        )
        try valid.validate()
        #expect(valid.normalizedPrompt == "user:\nHello")

        let cases: [(AppleFoundationModelsGenerateRequest, AppleFoundationModelsProtocolError)] = [
            (.init(requestID: "r", instructions: String(repeating: "x", count: AppleFoundationModelsProtocolV1.maximumInstructionsBytes + 1), messages: [.init(role: .user, content: "x")], timeoutMilliseconds: 1), .instructionsTooLarge),
            (.init(requestID: "r", messages: [], timeoutMilliseconds: 1), .invalidMessage),
            (.init(requestID: "r", messages: Array(repeating: .init(role: .user, content: "x"), count: AppleFoundationModelsProtocolV1.maximumMessages + 1), timeoutMilliseconds: 1), .tooManyMessages),
            (.init(requestID: "r", messages: [.init(role: .user, content: "")], timeoutMilliseconds: 1), .invalidMessage),
            (.init(requestID: "r", messages: [.init(role: .user, content: String(repeating: "x", count: AppleFoundationModelsProtocolV1.maximumMessageBytes + 1))], timeoutMilliseconds: 1), .messageTooLarge),
            (.init(requestID: "r", messages: [.init(role: .user, content: "x")], timeoutMilliseconds: 0), .invalidTimeout),
            (.init(requestID: "r", messages: [.init(role: .user, content: "x")], timeoutMilliseconds: AppleFoundationModelsProtocolV1.maximumTimeoutMilliseconds + 1), .invalidTimeout),
        ]
        for (request, expected) in cases {
            #expect(throws: expected) { try request.validate() }
        }
    }

    @Test func responseChunkAndQueueBoundsAreExplicit() throws {
        #expect(throws: AppleFoundationModelsProtocolError.responseTooLarge) {
            try AppleFoundationModelsGenerateResponse(
                requestID: "r",
                content: String(repeating: "x", count: AppleFoundationModelsProtocolV1.maximumAccumulatedResponseBytes + 1)
            )
        }
        #expect(throws: AppleFoundationModelsProtocolError.chunkTooLarge) {
            try AppleFoundationModelsStreamEvent(
                requestID: "r",
                sequence: 0,
                kind: .chunk,
                content: String(repeating: "x", count: AppleFoundationModelsProtocolV1.maximumChunkBytes + 1)
            )
        }
        var queue = AppleFoundationModelsBoundedEventQueue()
        for sequence in 0..<AppleFoundationModelsProtocolV1.maximumQueuedChunks {
            try queue.append(.init(requestID: "r", sequence: sequence, kind: .chunk, content: "x"))
        }
        #expect(throws: AppleFoundationModelsProtocolError.queueFull) {
            try queue.append(.init(requestID: "r", sequence: 99, kind: .chunk, content: "x"))
        }
        #expect(queue.drain().count == AppleFoundationModelsProtocolV1.maximumQueuedChunks)
        #expect(queue.drain().isEmpty)
    }

    @Test func callerPolicyRejectsCopiedUnsignedAndWrongIdentityHelpers() throws {
        let policy = AppleFoundationModelsCallerPolicy(expectedTeamIdentifier: "TEAM123")
        try policy.authorize(.init(
            identifier: AppleFoundationModelsCallerPolicy.helperIdentifier,
            teamIdentifier: "TEAM123",
            isPlatformSigned: true
        ))
        for identity in [
            AppleFoundationModelsCallerIdentity(identifier: "com.example.copy", teamIdentifier: "TEAM123", isPlatformSigned: true),
            AppleFoundationModelsCallerIdentity(identifier: AppleFoundationModelsCallerPolicy.helperIdentifier, teamIdentifier: "WRONG", isPlatformSigned: true),
            AppleFoundationModelsCallerIdentity(identifier: AppleFoundationModelsCallerPolicy.helperIdentifier, teamIdentifier: "TEAM123", isPlatformSigned: false),
        ] {
            #expect(throws: AppleFoundationModelsProtocolError.unauthorizedCaller) {
                try policy.authorize(identity)
            }
        }
    }

    @Test func versionSkewAndErrorsNeverContainBodyContent() throws {
        let marker = "private-prompt-marker"
        let request = AppleFoundationModelsGenerateRequest(
            protocolVersion: 99,
            requestID: "r",
            messages: [.init(role: .user, content: marker)],
            timeoutMilliseconds: 1
        )
        #expect(throws: AppleFoundationModelsProtocolError.unsupportedVersion) {
            try request.validate()
        }
        for error in [
            AppleFoundationModelsProtocolError.malformedPayload,
            .messageTooLarge,
            .unauthorizedCaller,
            .responseTooLarge,
            .timedOut,
        ] {
            #expect(!String(describing: error).contains(marker))
        }
    }
}
