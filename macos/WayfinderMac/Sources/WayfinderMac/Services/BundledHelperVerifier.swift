import Foundation
import Security

public enum BundledHelperVerificationError: Error, Equatable, LocalizedError, Sendable {
    case missingHelper
    case invalidCodeIntegrity
    case invalidManifest
    case desktopVersionMismatch
    case capabilitiesUnavailable
    case invalidHandshake

    public var errorDescription: String? {
        switch self {
        case .missingHelper:
            "The bundled gateway helper is missing."
        case .invalidCodeIntegrity:
            "Wayfinder could not verify the integrity of its bundled gateway."
        case .invalidManifest:
            "The bundled gateway manifest is invalid."
        case .desktopVersionMismatch:
            "The bundled gateway does not match this version of Wayfinder."
        case .capabilitiesUnavailable:
            "The bundled gateway did not complete its compatibility check."
        case .invalidHandshake:
            "The bundled gateway is not compatible with this version of Wayfinder."
        }
    }
}

/// Validates sealed app-bundle integrity and the helper protocol before the helper is executed.
public struct BundledHelperVerifier: Sendable {
    typealias IntegrityChecker = @Sendable (URL) -> Bool
    typealias DataLoader = @Sendable (URL, Int) throws -> Data
    typealias VersionLoader = @Sendable (URL) -> String?
    typealias CapabilitiesRunner = @Sendable (URL) async throws -> Data

    static let maximumManifestBytes = 32 * 1_024
    static let maximumCapabilitiesBytes = 64 * 1_024
    static let capabilitiesTimeoutNanoseconds: UInt64 = 5_000_000_000
    // Security/CSCommon.h exports this flag to C but not to Swift in every supported SDK.
    private static let noNetworkAccessFlag: UInt32 = 1 << 29

    private let isExecutable: @Sendable (String) -> Bool
    private let integrityChecker: IntegrityChecker
    private let dataLoader: DataLoader
    private let versionLoader: VersionLoader
    private let capabilitiesRunner: CapabilitiesRunner

    public init() {
        self.init(
            isExecutable: { FileManager.default.isExecutableFile(atPath: $0) },
            integrityChecker: { Self.checkCodeIntegrity($0) },
            dataLoader: { try Self.loadBoundedRegularFile($0, maximumBytes: $1) },
            versionLoader: { Self.loadBundleVersion($0) },
            capabilitiesRunner: { try await Self.runCapabilities($0) }
        )
    }

    init(
        isExecutable: @escaping @Sendable (String) -> Bool,
        integrityChecker: @escaping IntegrityChecker,
        dataLoader: @escaping DataLoader,
        versionLoader: @escaping VersionLoader,
        capabilitiesRunner: @escaping CapabilitiesRunner
    ) {
        self.isExecutable = isExecutable
        self.integrityChecker = integrityChecker
        self.dataLoader = dataLoader
        self.versionLoader = versionLoader
        self.capabilitiesRunner = capabilitiesRunner
    }

    public func verify(appBundleURL: URL, helperURL: URL) async throws -> URL {
        let expectedHelper = GatewayToolResolver.bundledHelperURL(in: appBundleURL).standardizedFileURL
        guard helperURL.standardizedFileURL == expectedHelper,
              isExecutable(expectedHelper.path) else {
            throw BundledHelperVerificationError.missingHelper
        }

        // Validate the outer signature with nested-code checks before executing any nested helper.
        guard integrityChecker(appBundleURL) else {
            throw BundledHelperVerificationError.invalidCodeIntegrity
        }

        let manifestURL = appBundleURL
            .appendingPathComponent("Contents/Resources/wayfinder-helper.json")
        let manifest: HelperManifest
        do {
            let data = try dataLoader(manifestURL, Self.maximumManifestBytes)
            manifest = try HelperVerifier.decodeManifest(data)
        } catch {
            throw BundledHelperVerificationError.invalidManifest
        }

        guard let desktopVersion = versionLoader(appBundleURL),
              desktopVersion == manifest.version else {
            throw BundledHelperVerificationError.desktopVersionMismatch
        }

        let capabilities: HelperCapabilities
        do {
            let data = try await capabilitiesRunner(expectedHelper)
            guard data.count <= Self.maximumCapabilitiesBytes else {
                throw BundledHelperVerificationError.capabilitiesUnavailable
            }
            capabilities = try HelperVerifier.decodeCapabilities(data)
        } catch {
            throw BundledHelperVerificationError.capabilitiesUnavailable
        }

        do {
            try HelperVerifier.verify(capabilities, against: manifest)
        } catch {
            throw BundledHelperVerificationError.invalidHandshake
        }

        // Close the validation-to-execution window as far as this resolver can: the caller receives
        // the URL only after a second full static-code validation of the outer app and nested code.
        guard integrityChecker(appBundleURL) else {
            throw BundledHelperVerificationError.invalidCodeIntegrity
        }
        return expectedHelper
    }

    private static func checkCodeIntegrity(_ appBundleURL: URL) -> Bool {
        var staticCode: SecStaticCode?
        guard SecStaticCodeCreateWithPath(appBundleURL as CFURL, [], &staticCode) == errSecSuccess,
              let staticCode else {
            return false
        }
        let flags = SecCSFlags(
            rawValue: kSecCSCheckNestedCode
                | kSecCSStrictValidate
                | kSecCSCheckAllArchitectures
                | kSecCSRestrictSymlinks
                | kSecCSRestrictToAppLike
                | noNetworkAccessFlag
        )
        return SecStaticCodeCheckValidity(staticCode, flags, nil) == errSecSuccess
    }

    static func loadBoundedRegularFile(_ url: URL, maximumBytes: Int) throws -> Data {
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true,
              values.isSymbolicLink != true,
              let fileSize = values.fileSize,
              fileSize >= 0,
              fileSize <= maximumBytes else {
            throw BundledHelperVerificationError.invalidManifest
        }
        let data = try Data(contentsOf: url, options: [.uncached])
        guard data.count <= maximumBytes else {
            throw BundledHelperVerificationError.invalidManifest
        }
        return data
    }

    private static func loadBundleVersion(_ appBundleURL: URL) -> String? {
        let infoURL = appBundleURL.appendingPathComponent("Contents/Info.plist")
        guard let data = try? loadBoundedRegularFile(infoURL, maximumBytes: 1_024 * 1_024),
              let plist = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil),
              let values = plist as? [String: Any] else {
            return nil
        }
        return values["CFBundleShortVersionString"] as? String
    }

    private static func runCapabilities(_ helperURL: URL) async throws -> Data {
        let result = try await BoundedProcessRunner.run(
            executable: helperURL,
            arguments: ["capabilities", "--json"],
            timeoutNanoseconds: capabilitiesTimeoutNanoseconds,
            maximumInputBytes: 1,
            maximumOutputBytes: maximumCapabilitiesBytes
        )
        guard result.exitCode == 0 else {
            throw BundledHelperVerificationError.capabilitiesUnavailable
        }
        return result.stdout
    }
}
