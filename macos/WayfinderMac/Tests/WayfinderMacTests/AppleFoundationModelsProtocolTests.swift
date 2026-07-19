import Foundation
import XCTest
@testable import WayfinderMacCore

final class AppleFoundationModelsProtocolTests: XCTestCase {
    func testAvailabilityCategoriesMapWithoutInferringReadiness() {
        let cases: [(AppleFoundationModelsNativeAvailability, AppleFoundationModelsAvailability)] = [
            (.available, .available),
            (.deviceNotEligible, .deviceNotEligible),
            (.appleIntelligenceNotEnabled, .appleIntelligenceNotEnabled),
            (.modelNotReady, .modelNotReady),
            (.unknownUnavailable, .unavailable),
        ]
        for (native, expected) in cases {
            XCTAssertEqual(
                AppleFoundationModelsAvailabilityQuery.map(
                    frameworkSupported: true,
                    native: native
                ),
                expected
            )
        }
    }

    func testOlderOSOrMissingFrameworkIsUnsupported() {
        XCTAssertEqual(
            AppleFoundationModelsAvailabilityQuery.map(
                frameworkSupported: false,
                native: .available
            ),
            .unsupported
        )
        XCTAssertEqual(
            AppleFoundationModelsAvailabilityQuery.map(
                frameworkSupported: true,
                native: nil
            ),
            .unsupported
        )
    }

    func testRequestVersionAndIdentifierAreBounded() throws {
        try AppleFoundationModelsAvailabilityRequest(requestID: "opaque-request").validate()
        assertThrows(.unsupportedVersion) {
            try AppleFoundationModelsAvailabilityRequest(
                protocolVersion: 2,
                requestID: "opaque-request"
            ).validate()
        }
        for requestID in ["", String(repeating: "x", count: 129)] {
            assertThrows(.invalidRequestID) {
                try AppleFoundationModelsAvailabilityRequest(requestID: requestID).validate()
            }
        }
    }

    func testAvailabilityWireValuesAreStableAndRoundTrip() throws {
        let response = AppleFoundationModelsAvailabilityResponse(
            requestID: "opaque-request",
            availability: .modelNotReady
        )
        let data = try JSONEncoder().encode(response)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["protocolVersion"] as? Int, 1)
        XCTAssertEqual(object["requestID"] as? String, "opaque-request")
        XCTAssertEqual(object["availability"] as? String, "model-not-ready")
        XCTAssertEqual(
            try JSONDecoder().decode(
                AppleFoundationModelsAvailabilityResponse.self,
                from: data
            ),
            response
        )
    }

    func testProtocolErrorsDoNotEchoRequestContent() {
        let marker = "prompt-or-secret-marker"
        let rendered = String(describing: AppleFoundationModelsProtocolError.invalidRequestID)
        XCTAssertFalse(rendered.contains(marker))
    }

    func testMalformedAndOversizedPayloadsAreRejectedDeterministically() {
        assertThrows(.malformedPayload) {
            _ = try AppleFoundationModelsWireCodec.decode(
                AppleFoundationModelsGenerateRequest.self,
                from: Data("not-json".utf8)
            )
        }
        assertThrows(.requestTooLarge) {
            _ = try AppleFoundationModelsWireCodec.decode(
                AppleFoundationModelsGenerateRequest.self,
                from: Data(repeating: 0, count: AppleFoundationModelsProtocolV1.maximumEncodedRequestBytes + 1)
            )
        }
    }

    func testGenerationRequestEnforcesContentCountAndDeadlineBounds() throws {
        let valid = AppleFoundationModelsGenerateRequest(
            requestID: "request-1",
            instructions: "Answer briefly.",
            messages: [.init(role: .user, content: "Hello")],
            timeoutMilliseconds: 1_000
        )
        try valid.validate()
        XCTAssertEqual(valid.normalizedPrompt, "user:\nHello")

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
            assertThrows(expected) { try request.validate() }
        }
    }

    func testResponseChunkAndQueueBoundsAreExplicit() throws {
        assertThrows(.responseTooLarge) {
            _ = try AppleFoundationModelsGenerateResponse(
                requestID: "r",
                content: String(repeating: "x", count: AppleFoundationModelsProtocolV1.maximumAccumulatedResponseBytes + 1)
            )
        }
        assertThrows(.chunkTooLarge) {
            _ = try AppleFoundationModelsStreamEvent(
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
        assertThrows(.queueFull) {
            try queue.append(.init(requestID: "r", sequence: 99, kind: .chunk, content: "x"))
        }
        XCTAssertEqual(queue.drain().count, AppleFoundationModelsProtocolV1.maximumQueuedChunks)
        XCTAssertTrue(queue.drain().isEmpty)
    }

    func testCallerPolicyRejectsCopiedUnsignedAndWrongIdentityHelpers() throws {
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
            assertThrows(.unauthorizedCaller) {
                try policy.authorize(identity)
            }
        }
    }

    func testVersionSkewAndErrorsNeverContainBodyContent() throws {
        let marker = "private-prompt-marker"
        let request = AppleFoundationModelsGenerateRequest(
            protocolVersion: 99,
            requestID: "r",
            messages: [.init(role: .user, content: marker)],
            timeoutMilliseconds: 1
        )
        assertThrows(.unsupportedVersion) {
            try request.validate()
        }
        for error in [
            AppleFoundationModelsProtocolError.malformedPayload,
            .messageTooLarge,
            .unauthorizedCaller,
            .responseTooLarge,
            .timedOut,
        ] {
            XCTAssertFalse(String(describing: error).contains(marker))
        }
    }

    private func assertThrows(
        _ expected: AppleFoundationModelsProtocolError,
        _ expression: () throws -> Void,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(try expression(), file: file, line: line) { error in
            XCTAssertEqual(error as? AppleFoundationModelsProtocolError, expected, file: file, line: line)
        }
    }
}
