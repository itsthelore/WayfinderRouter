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
}
