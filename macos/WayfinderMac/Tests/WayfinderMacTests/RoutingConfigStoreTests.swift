import Foundation
import XCTest
@testable import WayfinderMacCore

final class RoutingConfigStoreTests: XCTestCase {
    func testLoadMissingConfigReportsMissingConfig() async {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathComponent("wayfinder-router.toml")
        let store = RoutingConfigStore(configURL: url) { _, _ in
            XCTFail("Missing config should not invoke CLI")
            return RoutingCommandResult(exitCode: 0)
        }

        do {
            _ = try await store.load()
            XCTFail("Expected missing config error")
        } catch let error as RoutingConfigStoreError {
            XCTAssertEqual(error, .missingConfig(url.path))
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testLoadBinaryRoutingState() async throws {
        let url = try makeConfig()
        let store = RoutingConfigStore(configURL: url) { arguments, _ in
            XCTAssertEqual(arguments, ["config", "read-routing", "--path", url.path])
            return RoutingCommandResult(exitCode: 0, stdout: Self.binaryJSON)
        }

        let state = try await store.load()

        XCTAssertEqual(state.mode, .binary)
        XCTAssertEqual(state.threshold, 0.42)
        XCTAssertEqual(state.tiers.count, 2)
        XCTAssertFalse(state.dirty)
    }

    func testLoadTieredRoutingState() async throws {
        let url = try makeConfig()
        let store = RoutingConfigStore(configURL: url) { _, _ in
            RoutingCommandResult(exitCode: 0, stdout: Self.tieredJSON)
        }

        let state = try await store.load()

        XCTAssertEqual(state.mode, .tiered)
        XCTAssertEqual(state.tiers.map(\.model), ["local", "mid", "cloud"])
        XCTAssertEqual(state.tiers[0].minScore, 0.0)
        XCTAssertFalse(state.tiers[0].editable)
    }

    func testLoadClassifierRoutingState() async throws {
        let url = try makeConfig()
        let store = RoutingConfigStore(configURL: url) { _, _ in
            RoutingCommandResult(exitCode: 0, stdout: Self.classifierJSON)
        }

        let state = try await store.load()

        XCTAssertEqual(state.mode, .classifier)
        XCTAssertEqual(state.classifierModels, ["small", "large"])
    }

    func testSaveAppliesRoutingAndClearsThroughCLI() async throws {
        let url = try makeConfig()
        let capture = RoutingTestCapture()
        let store = RoutingConfigStore(configURL: url) { arguments, stdin in
            XCTAssertEqual(arguments, ["config", "apply-routing", "--path", url.path])
            await capture.set(stdin ?? "")
            return RoutingCommandResult(exitCode: 0)
        }

        var state = RoutingSettingsState()
        state.threshold = 0.7
        state.weights[0].value = 4.0

        try await store.save(state)

        let capturedStdin = await capture.value
        XCTAssertTrue(capturedStdin.contains("threshold = 0.7"))
        XCTAssertTrue(capturedStdin.contains("word_count = 4.0"))
        XCTAssertFalse(capturedStdin.contains("[gateway]"))
    }

    func testSaveSurfacesCLIError() async throws {
        let url = try makeConfig()
        let store = RoutingConfigStore(configURL: url) { _, _ in
            RoutingCommandResult(exitCode: 1, stderr: "bad routing")
        }

        do {
            try await store.save(RoutingSettingsState())
            XCTFail("Expected CLI error")
        } catch let error as RoutingConfigStoreError {
            XCTAssertEqual(error, .cli("bad routing"))
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    private func makeConfig() throws -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent("wayfinder-router.toml")
        try "[routing]\nthreshold = 0.5\n".write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    private static let weightsJSON = """
    [
      {"id":"word_count","label":"Word Count","value":3.0,"default":3.0},
      {"id":"heading_count","label":"Heading Count","value":1.5,"default":1.5}
    ]
    """

    private static let binaryJSON = """
    {
      "mode": "binary",
      "threshold": 0.42,
      "tiers": [
        {"min_score": 0.0, "model": "local"},
        {"min_score": 0.42, "model": "cloud"}
      ],
      "weights": \(weightsJSON)
    }
    """

    private static let tieredJSON = """
    {
      "mode": "tiered",
      "tiers": [
        {"min_score": 0.0, "model": "local"},
        {"min_score": 0.35, "model": "mid"},
        {"min_score": 0.75, "model": "cloud"}
      ],
      "weights": \(weightsJSON)
    }
    """

    private static let classifierJSON = """
    {
      "mode": "classifier",
      "models": ["small", "large"],
      "weights": \(weightsJSON)
    }
    """
}

private actor RoutingTestCapture {
    private var stored = ""

    var value: String { stored }

    func set(_ value: String) {
        stored = value
    }
}
