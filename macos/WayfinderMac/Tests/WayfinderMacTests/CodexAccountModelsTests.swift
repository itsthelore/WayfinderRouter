import Foundation
import XCTest
@testable import WayfinderMacCore

final class CodexAccountModelsTests: XCTestCase {
    func testAccountContractDecodesEveryNormalizedState() throws {
        XCTAssertEqual(try decodeAccount(#"{"status":"signed_out"}"#), .signedOut)

        let browser = try decodeAccount(
            #"{"status":"awaiting_browser","login":{"id":"browser-1","flow":"browser","url":"https://auth.openai.com/start"}}"#
        )
        XCTAssertEqual(
            browser,
            .awaitingBrowser(CodexPendingLogin(
                id: "browser-1",
                flow: .browser,
                url: URL(string: "https://auth.openai.com/start")!
            ))
        )

        let device = try decodeAccount(
            #"{"status":"awaiting_device_code","login":{"id":"device-1","flow":"device_code","url":"https://auth.openai.com/device","user_code":"ABCD-EFGH"}}"#
        )
        XCTAssertEqual(
            device,
            .awaitingDeviceCode(CodexPendingLogin(
                id: "device-1",
                flow: .deviceCode,
                url: URL(string: "https://auth.openai.com/device")!,
                userCode: "ABCD-EFGH"
            ))
        )

        XCTAssertEqual(
            try decodeAccount(#"{"status":"connected","account":{"email":"tom@example.com","plan":"Plus"}}"#),
            .connected(CodexAccountProfile(email: "tom@example.com", plan: "Plus"))
        )
        XCTAssertEqual(
            try decodeAccount(#"{"status":"reauth_required","detail":"Session expired"}"#),
            .reauthenticationRequired(detail: "Session expired")
        )
        XCTAssertEqual(
            try decodeAccount(#"{"status":"unavailable","detail":"Runtime missing"}"#),
            .unavailable(detail: "Runtime missing")
        )
    }

    func testLoginContractRejectsInsecureOrCredentialBearingURLs() {
        XCTAssertThrowsError(try decodeAccount(
            #"{"status":"awaiting_browser","login":{"id":"one","flow":"browser","url":"http://auth.openai.com/start"}}"#
        ))
        XCTAssertThrowsError(try decodeAccount(
            #"{"status":"awaiting_browser","login":{"id":"one","flow":"browser","url":"https://user:password@auth.openai.com/start"}}"#
        ))
        XCTAssertThrowsError(try decodeAccount(
            #"{"status":"awaiting_browser","login":{"id":"one","flow":"browser","url":"https://example.com/fake-openai-login"}}"#
        ))
    }

    func testLoginContractRejectsMismatchedFlowAndMissingDeviceCode() {
        XCTAssertThrowsError(try decodeAccount(
            #"{"status":"awaiting_browser","login":{"id":"one","flow":"device_code","url":"https://auth.openai.com/device","user_code":"CODE"}}"#
        ))
        XCTAssertThrowsError(try decodeAccount(
            #"{"status":"awaiting_device_code","login":{"id":"one","flow":"device_code","url":"https://auth.openai.com/device"}}"#
        ))
    }

    func testAccountContractBoundsDisplayedValues() {
        let oversizedPlan = String(repeating: "p", count: 129)
        XCTAssertThrowsError(try decodeAccount(
            #"{"status":"connected","account":{"email":null,"plan":"\#(oversizedPlan)"}}"#
        ))
        XCTAssertThrowsError(try decodeAccount(
            "{\"status\":\"unavailable\",\"detail\":\"bad\\u0000value\"}"
        ))
    }

    func testModelCatalogIsBoundedAndDeduplicated() throws {
        let response = try JSONDecoder().decode(
            CodexModelsResponse.self,
            from: Data(#"{"models":[{"id":"gpt-5.6-sol","display_name":"GPT-5.6 Sol"}]}"#.utf8)
        )
        XCTAssertEqual(response.models, [CodexModel(id: "gpt-5.6-sol", displayName: "GPT-5.6 Sol")])

        XCTAssertThrowsError(try JSONDecoder().decode(
            CodexModelsResponse.self,
            from: Data(#"{"models":[{"id":"duplicate"},{"id":"duplicate"}]}"#.utf8)
        ))

        let oversized = CodexModelsResponse.maximumModelCount + 1
        let models = (0..<oversized).map { #"{"id":"model-\#($0)"}"# }.joined(separator: ",")
        XCTAssertThrowsError(try JSONDecoder().decode(
            CodexModelsResponse.self,
            from: Data("{\"models\":[\(models)]}".utf8)
        ))
    }

    private func decodeAccount(_ json: String) throws -> CodexAccountSnapshot {
        try JSONDecoder().decode(CodexAccountSnapshot.self, from: Data(json.utf8))
    }
}
