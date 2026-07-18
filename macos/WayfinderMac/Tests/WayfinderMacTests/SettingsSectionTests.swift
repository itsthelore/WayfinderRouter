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

    func testSettingsNotificationDeepLinksToAccountsAndDefaultsToGateway() {
        let accounts = Notification(
            name: .wayfinderOpenSettings,
            object: SettingsSection.accounts
        )
        XCTAssertEqual(SettingsWindowNavigation.section(from: accounts), .accounts)
        XCTAssertEqual(
            SettingsWindowNavigation.section(
                from: Notification(name: .wayfinderOpenSettings)
            ),
            .gateway
        )

        let navigation = SettingsWindowNavigation()
        navigation.select(SettingsWindowNavigation.section(from: accounts))
        XCTAssertEqual(navigation.selectedSection, .accounts)
    }

    func testHelpSectionUsesExpectedLabelAndSymbol() {
        XCTAssertEqual(SettingsSection.help.rawValue, "Help")
        XCTAssertEqual(SettingsSection.help.symbolName, "questionmark.circle")
        XCTAssertEqual(SettingsSection.about.rawValue, "About")
        XCTAssertEqual(SettingsSection.about.symbolName, "info.circle")
    }
}
