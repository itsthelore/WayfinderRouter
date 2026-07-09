import XCTest
@testable import WayfinderMacCore

final class RoutingSettingsStateTests: XCTestCase {
    func testDefaultThresholdAndWeightsRemainVisibleInState() {
        let state = RoutingSettingsState()

        XCTAssertEqual(RoutingSettingsState.defaultThreshold, 0.5)
        XCTAssertEqual(state.threshold, 0.5)
        XCTAssertEqual(state.tiers.map(\.minScore), [0.0, 0.5])

        let defaultsByID = Dictionary(uniqueKeysWithValues: state.weights.map { ($0.id, $0.defaultValue) })
        XCTAssertEqual(defaultsByID["word_count"], 3.0)
        XCTAssertEqual(defaultsByID["list_item_count"], 2.0)
        XCTAssertEqual(defaultsByID["heading_count"], 1.5)
        XCTAssertEqual(defaultsByID["code_block_count"], 1.5)
        XCTAssertEqual(defaultsByID["table_row_count"], 1.0)
        XCTAssertEqual(defaultsByID["link_count"], 1.0)
        XCTAssertEqual(defaultsByID["max_heading_depth"], 1.0)
        XCTAssertEqual(defaultsByID["reasoning_term_count"], 0.0)
        XCTAssertEqual(defaultsByID["math_symbol_count"], 0.0)
        XCTAssertEqual(defaultsByID["constraint_term_count"], 0.0)
        XCTAssertEqual(defaultsByID["question_count"], 0.0)
    }

    func testResetWeightsRestoresShippedDefaults() {
        var state = RoutingSettingsState()
        for index in state.weights.indices {
            state.weights[index].value = Double(index + 1)
        }

        state.resetWeightsToDefaults()

        for row in state.weights {
            XCTAssertEqual(row.value, row.defaultValue, row.id)
        }
    }

    func testHelpMetadataExistsForEveryWeightRow() {
        for row in RoutingSettingsState.defaultWeights {
            let helpText = RoutingSettingsState.weightHelpText[row.id] ?? ""
            XCTAssertFalse(helpText.isEmpty, row.id)
        }
    }
}
