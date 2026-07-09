import XCTest
@testable import WayfinderMacCore

final class SettingsSectionTests: XCTestCase {
    func testHelpIsSeparateFromAboutAndOrderedBeforeIt() {
        let sections = SettingsSection.allCases

        XCTAssertTrue(sections.contains(.help))
        XCTAssertTrue(sections.contains(.about))
        XCTAssertNotEqual(SettingsSection.help.rawValue, SettingsSection.about.rawValue)
        XCTAssertLessThan(
            sections.firstIndex(of: .help) ?? sections.endIndex,
            sections.firstIndex(of: .about) ?? sections.endIndex
        )
    }

    func testHelpSectionUsesExpectedLabelAndSymbol() {
        XCTAssertEqual(SettingsSection.help.rawValue, "Help")
        XCTAssertEqual(SettingsSection.help.symbolName, "questionmark.circle")
        XCTAssertEqual(SettingsSection.about.rawValue, "About")
        XCTAssertEqual(SettingsSection.about.symbolName, "info.circle")
    }
}
