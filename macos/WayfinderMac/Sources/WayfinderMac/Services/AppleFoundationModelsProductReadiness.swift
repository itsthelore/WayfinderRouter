import Foundation
import Security

public struct WayfinderSignedComponentIdentity: Equatable, Sendable {
    public let identifier: String
    public let teamIdentifier: String?
    public let isPlatformSigned: Bool

    public init(identifier: String, teamIdentifier: String?, isPlatformSigned: Bool) {
        self.identifier = identifier
        self.teamIdentifier = teamIdentifier
        self.isPlatformSigned = isPlatformSigned
    }
}

public enum AppleFoundationModelsProductReadiness: Equatable, Sendable {
    case ready(teamIdentifier: String)
    case incompleteOrInvalid

    public var isReady: Bool {
        if case .ready = self { return true }
        return false
    }

    public static let requiredIdentifiers = [
        "com.wayfinder.router.mac",
        AppleFoundationModelsCallerPolicy.helperIdentifier,
        "com.wayfinder.CredentialBroker",
        "com.wayfinder.FoundationModelBroker",
    ]

    /// Pure production-readiness policy kept separate for deterministic tests.
    public static func evaluate(_ identities: [WayfinderSignedComponentIdentity]) -> Self {
        guard identities.count == requiredIdentifiers.count else { return .incompleteOrInvalid }
        let grouped = Dictionary(grouping: identities, by: \.identifier)
        guard grouped.count == requiredIdentifiers.count,
              requiredIdentifiers.allSatisfy({ grouped[$0]?.count == 1 }),
              requiredIdentifiers.allSatisfy({ grouped[$0]?.first?.isPlatformSigned == true }),
              let teamIdentifier = grouped[requiredIdentifiers[0]]?.first?.teamIdentifier,
              !teamIdentifier.isEmpty,
              requiredIdentifiers.allSatisfy({ grouped[$0]?.first?.teamIdentifier == teamIdentifier }) else {
            return .incompleteOrInvalid
        }
        return .ready(teamIdentifier: teamIdentifier)
    }

    public static func current(appBundleURL: URL = Bundle.main.bundleURL) -> Self {
        let gateway = appBundleURL
            .appendingPathComponent("Contents/Helpers/WayfinderGateway.app")
        let urls = [
            appBundleURL,
            gateway,
            gateway.appendingPathComponent("Contents/XPCServices/com.wayfinder.CredentialBroker.xpc"),
            gateway.appendingPathComponent("Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"),
        ]
        let identities = urls.compactMap(signingIdentity)
        return evaluate(identities)
    }

    private static func signingIdentity(at url: URL) -> WayfinderSignedComponentIdentity? {
        var staticCode: SecStaticCode?
        var information: CFDictionary?
        var platformRequirement: SecRequirement?
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &staticCode) == errSecSuccess,
              let staticCode,
              SecCodeCopySigningInformation(
                staticCode,
                SecCSFlags(rawValue: kSecCSSigningInformation),
                &information
              ) == errSecSuccess,
              let values = information as? [String: Any],
              let identifier = values[kSecCodeInfoIdentifier as String] as? String,
              SecRequirementCreateWithString("anchor apple generic" as CFString, [], &platformRequirement) == errSecSuccess,
              let platformRequirement else {
            return nil
        }
        return WayfinderSignedComponentIdentity(
            identifier: identifier,
            teamIdentifier: values[kSecCodeInfoTeamIdentifier as String] as? String,
            isPlatformSigned: SecStaticCodeCheckValidity(staticCode, [], platformRequirement) == errSecSuccess
        )
    }
}
