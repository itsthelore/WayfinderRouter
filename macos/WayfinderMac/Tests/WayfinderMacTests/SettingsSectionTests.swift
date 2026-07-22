import XCTest
@testable import WayfinderMacCore

final class SettingsSectionTests: XCTestCase {
    func testOpenAIPlatformKeysRemainDistinctFromChatGPTAccountAccess() {
        XCTAssertEqual(ProviderKind.openAI.rawValue, "OpenAI API")
        XCTAssertEqual(ProviderKind.openAI.credentialDetail.displayName, "OpenAI Platform API")
        XCTAssertEqual(CredentialStatus.keyMissing.title, "Not connected")
    }
    func testOnlyShippedSettingsSectionsAreListed() {
        XCTAssertEqual(
            SettingsSection.allCases,
            [.connections, .gateway, .routing, .about]
        )
    }

    func testAccountsAndKeysAreConsolidatedAsConnections() {
        XCTAssertEqual(SettingsSection.connections.rawValue, "Connections")
        XCTAssertEqual(SettingsSection.connections.symbolName, "link")
        XCTAssertEqual(ConnectionKind.allCases.first, .chatGPT)
        XCTAssertEqual(ConnectionKind.openAI.provider, .openAI)
    }

    func testSettingsNotificationDeepLinksToConnectionsAndDefaultsToConnections() {
        let accounts = Notification(
            name: .wayfinderOpenSettings,
            object: SettingsSection.connections
        )
        XCTAssertEqual(SettingsWindowNavigation.section(from: accounts), .connections)
        XCTAssertEqual(
            SettingsWindowNavigation.section(
                from: Notification(name: .wayfinderOpenSettings)
            ),
            .connections
        )

        let navigation = SettingsWindowNavigation()
        navigation.select(SettingsWindowNavigation.section(from: accounts))
        XCTAssertEqual(navigation.selectedSection, .connections)
    }

    func testAboutConsolidatesLegacyHelpAndPrivacyNavigation() throws {
        XCTAssertEqual(SettingsSection.about.rawValue, "About")
        XCTAssertEqual(SettingsSection.about.symbolName, "info.circle")
        XCTAssertEqual(try JSONDecoder().decode(SettingsSection.self, from: Data(#""Help""#.utf8)), .about)
        XCTAssertEqual(try JSONDecoder().decode(SettingsSection.self, from: Data(#""Privacy""#.utf8)), .about)
    }
}
