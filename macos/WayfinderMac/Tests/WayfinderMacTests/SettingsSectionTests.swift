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

    func testOnlyShippedSettingsSectionsAreListed() {
        XCTAssertEqual(
            SettingsSection.allCases,
            [.gateway, .routing, .accounts, .keys, .privacy, .help, .about]
        )
    }

    func testAccountsIsSeparateFromKeys() {
        XCTAssertEqual(SettingsSection.accounts.rawValue, "Accounts")
        XCTAssertEqual(SettingsSection.accounts.symbolName, "person.crop.circle")
        XCTAssertLessThan(
            SettingsSection.allCases.firstIndex(of: .accounts)!,
            SettingsSection.allCases.firstIndex(of: .keys)!
        )
    }

    func testHelpSectionUsesExpectedLabelAndSymbol() {
        XCTAssertEqual(SettingsSection.help.rawValue, "Help")
        XCTAssertEqual(SettingsSection.help.symbolName, "questionmark.circle")
        XCTAssertEqual(SettingsSection.about.rawValue, "About")
        XCTAssertEqual(SettingsSection.about.symbolName, "info.circle")
    }
}
