import Foundation

public struct GatewayToolResolver: Sendable {
    public typealias BundledGatewayVerifier = @Sendable (_ appBundleURL: URL, _ helperURL: URL) async throws -> Void

    private let environment: [String: String]
    private let appBundleURL: URL
    private let isExecutable: @Sendable (String) -> Bool
    private let bundledGatewayVerifier: BundledGatewayVerifier

    public init(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        appBundleURL: URL = Bundle.main.bundleURL,
        isExecutable: @escaping @Sendable (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) },
        bundledGatewayVerifier: @escaping BundledGatewayVerifier = { appBundleURL, helperURL in
            _ = try await BundledHelperVerifier().verify(
                appBundleURL: appBundleURL,
                helperURL: helperURL
            )
        }
    ) {
        self.environment = environment
        self.appBundleURL = appBundleURL
        self.isExecutable = isExecutable
        self.bundledGatewayVerifier = bundledGatewayVerifier
    }

    /// Resolves only Wayfinder's authenticated bundled gateway. There is deliberately no PATH or
    /// Homebrew fallback for the gateway in production.
    public func resolveGateway() async throws -> URL {
        let bundled = Self.bundledHelperURL(in: appBundleURL)
        guard isExecutable(bundled.path) else {
            throw BundledHelperVerificationError.missingHelper
        }
        try await bundledGatewayVerifier(appBundleURL, bundled)
        return bundled
    }

    /// Resolves ancillary user-managed runtimes such as Ollama.
    public func resolveRuntime(_ name: String) -> URL? {
        guard name != "wayfinder-router" else { return nil }
        let inherited = environment["PATH", default: ""].split(separator: ":").map(String.init)
        let directories = inherited + ["/opt/homebrew/bin", "/usr/local/bin"]
        for directory in directories {
            let path = URL(fileURLWithPath: directory).appendingPathComponent(name).path
            if isExecutable(path) { return URL(fileURLWithPath: path) }
        }
        return nil
    }

    public func resolvesRuntime(_ executable: String) -> Bool {
        resolveRuntime(executable) != nil
    }

    static func bundledHelperURL(in appBundleURL: URL) -> URL {
        appBundleURL
            .appendingPathComponent("Contents/Helpers/WayfinderGateway.app")
            .appendingPathComponent("Contents/MacOS/wayfinder-router")
    }
}

public struct SetupCommand: Equatable, Sendable {
    public let executable: URL
    public let arguments: [String]
}

public enum SetupCommandPlan {
    public static func make(tool: URL, presetID: String, configPath: String) throws -> [SetupCommand] {
        guard SetupPreset.commandPresetIDs.contains(presetID) else { throw SetupFailure.invalidPreset }
        guard FileManager.default.isExecutableFile(atPath: tool.path) else { throw SetupFailure.bundledHelperInvalid }
        let allowedRoot = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Wayfinder").standardizedFileURL.path
        let target = URL(fileURLWithPath: configPath).standardizedFileURL.path
        guard target.hasPrefix(allowedRoot + "/") else { throw SetupFailure.unsafeConfigPath }
        return [
            SetupCommand(executable: tool, arguments: ["app-setup-init", "--preset", presetID, "--path", target]),
            SetupCommand(executable: tool, arguments: ["service", "uninstall"]),
            SetupCommand(executable: tool, arguments: ["service", "install", "--config", target]),
        ]
    }
}
