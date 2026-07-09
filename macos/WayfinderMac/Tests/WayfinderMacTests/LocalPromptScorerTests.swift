import XCTest
@testable import WayfinderMacCore

final class LocalPromptScorerTests: XCTestCase {
    func testTrivialPromptRoutesLocal() throws {
        let decision = try LocalPromptScorer().analyse(prompt: "Say hello.")

        XCTAssertEqual(decision.route, .local)
        XCTAssertEqual(decision.selectedModel, "local")
        XCTAssertEqual(decision.score, 0.0)
    }

    func testComplexPromptScoresHigherThanTrivial() throws {
        let scorer = LocalPromptScorer()
        let trivial = try scorer.analyse(prompt: "Say hello.")
        let complex = try scorer.analyse(prompt: Self.complexPrompt)

        XCTAssertGreaterThan(complex.score, trivial.score)
        XCTAssertFalse(complex.features.isEmpty)
    }

    func testAnalyseUsesPreviewThreshold() throws {
        let scorer = LocalPromptScorer()

        let localDecision = try scorer.analyse(
            prompt: Self.complexPrompt,
            threshold: 0.99
        )
        let cloudDecision = try scorer.analyse(
            prompt: Self.complexPrompt,
            threshold: 0.01
        )

        XCTAssertEqual(localDecision.route, .local)
        XCTAssertEqual(cloudDecision.route, .cloud)
    }

    func testAnalyseUsesPreviewWeights() throws {
        var weights = RoutingSettingsState.defaultWeights
        for index in weights.indices {
            weights[index].value = weights[index].id == "question_count" ? 10.0 : 0.0
        }

        let decision = try LocalPromptScorer().analyse(
            prompt: "Why? How? What?",
            threshold: 0.5,
            weights: weights
        )

        XCTAssertEqual(decision.route, .cloud)
        XCTAssertEqual(decision.features.first?.name, "question_count")
    }

    func testFeatureExtractionIgnoresCodeFenceContents() {
        let features = LocalPromptScorer().extractFeatures(
            from: """
            ```
            ## Not a heading
            - not a list
            | a | b |
            ```
            """
        )

        XCTAssertEqual(features["heading_count"], 0)
        XCTAssertEqual(features["list_item_count"], 0)
        XCTAssertEqual(features["table_row_count"], 0)
        XCTAssertEqual(features["code_block_count"], 1)
    }

    private static let complexPrompt = """
    # Build the reporting pipeline

    ## Context

    We need a deterministic batch pipeline that ingests events and emits a daily
    report, with retries and backfill, across three environments.

    ## Steps

    - Parse the input manifest
    - Validate every row against the schema
    - Deduplicate by event id
    - Aggregate per day
    - Render the report
    - Upload the artifact
    - Notify the channel

    ```python
    def pipeline(rows):
        return aggregate(rows)
    ```

    | Field | Type |
    | --- | --- |
    | id | string |
    | ts | int |
    """
}
