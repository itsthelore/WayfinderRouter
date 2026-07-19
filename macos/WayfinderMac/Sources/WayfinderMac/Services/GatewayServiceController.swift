import Foundation

public struct GatewayServiceController: Sendable {
    public static let launchdLabel = "com.wayfinder-router.gateway"
    public static let defaultHost = "127.0.0.1"
    public static let defaultPort = 8088

    private let launchAgentURL: URL
    private let healthProbe: @Sendable (URL) async -> GatewayHealth?

    public init(launchAgentURL: URL = Self.defaultLaunchAgentURL()) {
        self.launchAgentURL = launchAgentURL
        self.healthProbe = { url in
            await Self.probeHealth(url: url)
        }
    }

    init(
        launchAgentURL: URL,
        healthProbe: @escaping @Sendable (URL) async -> GatewayHealth?
    ) {
        self.launchAgentURL = launchAgentURL
        self.healthProbe = healthProbe
    }

    public func status(expectedGateway: URL? = nil) async -> GatewayServiceStatus {
        let installedConfiguration = installedLaunchConfiguration()
        let launchConfiguration = installedConfiguration ?? defaultLaunchConfiguration()
        let loaded = await isLoaded()
        let health: GatewayHealth?
        let executableMatches = expectedGateway.map {
            installedConfiguration?.usesGateway(at: $0) == true
        } ?? true
        if installedConfiguration != nil,
           executableMatches,
           let healthURL = launchConfiguration.loopbackHealthURL {
            health = await healthProbe(healthURL)
        } else {
            health = nil
        }
        return GatewayServiceStatus(
            installed: FileManager.default.fileExists(atPath: launchAgentURL.path),
            loaded: loaded,
            launchConfiguration: launchConfiguration,
            health: health
        )
    }

    public func statusWithoutHealthProbe() async -> GatewayServiceStatus {
        let launchConfiguration = installedLaunchConfiguration() ?? defaultLaunchConfiguration()
        return GatewayServiceStatus(
            installed: FileManager.default.fileExists(atPath: launchAgentURL.path),
            loaded: await isLoaded(),
            launchConfiguration: launchConfiguration,
            health: nil
        )
    }

    public func restart() async throws {
        let uid = getuid()
        let result = await runLaunchctl(arguments: [
            "kickstart",
            "-k",
            "gui/\(uid)/\(Self.launchdLabel)",
        ])
        guard result.isSuccess else {
            throw GatewayServiceControllerError.restartFailed(result.stderr)
        }
    }

    public func restart(expectedGateway: URL) async throws {
        guard installedLaunchConfiguration()?.usesGateway(at: expectedGateway) == true else {
            throw GatewayServiceControllerError.serviceNeedsRepair
        }
        try await restart()
    }

    public static func defaultConfigPath() -> String {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Wayfinder/wayfinder-router.toml")
            .path
    }

    public static func defaultLaunchAgentURL() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(launchdLabel).plist")
    }

    public static func launchConfiguration(fromProgramArguments arguments: [String]) -> GatewayLaunchConfiguration {
        let executablePath = arguments.first
        let host = argumentValue(after: "--host", in: arguments) ?? defaultHost
        let port = argumentValue(after: "--port", in: arguments)
            .flatMap(Int.init)
            .flatMap { GatewayLaunchConfiguration.validPortRange.contains($0) ? $0 : nil }
            ?? defaultPort
        let configPath = argumentValue(after: "--config", in: arguments) ?? defaultConfigPath()
        return GatewayLaunchConfiguration(
            host: host,
            port: port,
            configPath: configPath,
            executablePath: executablePath
        )
    }

    public static func decodeHealth(_ data: Data) throws -> GatewayHealth {
        try JSONDecoder().decode(GatewayHealth.self, from: data)
    }

    /// Returns a bounded, parsed LaunchAgent configuration without contacting the configured
    /// endpoint or invoking launchctl. Runtime trust checks use this before any loopback request.
    public func installedLaunchConfiguration() -> GatewayLaunchConfiguration? {
        guard let values = try? launchAgentURL.resourceValues(
            forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey]
        ),
        values.isRegularFile == true,
        values.isSymbolicLink != true,
        let fileSize = values.fileSize,
        fileSize >= 0,
        fileSize <= 64 * 1_024,
        let data = try? Data(contentsOf: launchAgentURL, options: [.uncached]),
        data.count <= 64 * 1_024,
        let plist = try? PropertyListSerialization.propertyList(
            from: data,
            options: [],
            format: nil
        ) as? [String: Any],
        let arguments = plist["ProgramArguments"] as? [String],
        !arguments.isEmpty else {
            return nil
        }
        return Self.launchConfiguration(fromProgramArguments: arguments)
    }

    private func defaultLaunchConfiguration() -> GatewayLaunchConfiguration {
        GatewayLaunchConfiguration(
            host: Self.defaultHost,
            port: Self.defaultPort,
            configPath: Self.defaultConfigPath()
        )
    }

    private func isLoaded() async -> Bool {
        let uid = getuid()
        let result = await runLaunchctl(arguments: [
            "print",
            "gui/\(uid)/\(Self.launchdLabel)",
        ])
        return result.isSuccess
    }

    private func runLaunchctl(arguments: [String]) async -> ProcessResult {
        await Task.detached {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            process.arguments = arguments

            let stderr = Pipe()
            process.standardOutput = FileHandle(forWritingAtPath: "/dev/null")
            process.standardError = stderr

            do {
                try process.run()
            } catch {
                return ProcessResult(exitCode: 1, stderr: "launchctl: \(error.localizedDescription)")
            }

            process.waitUntilExit()
            let errorData = stderr.fileHandleForReading.readDataToEndOfFile()
            let errorText = String(data: errorData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return ProcessResult(exitCode: process.terminationStatus, stderr: errorText)
        }.value
    }

    private static func argumentValue(after flag: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: flag) else { return nil }
        let valueIndex = arguments.index(after: index)
        guard arguments.indices.contains(valueIndex) else { return nil }
        return arguments[valueIndex]
    }

    private static func probeHealth(url: URL) async -> GatewayHealth? {
        var request = URLRequest(url: url)
        request.timeoutInterval = 1.5

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return try decodeHealth(data)
        } catch {
            return nil
        }
    }
}

public struct GatewayServiceStatus: Equatable, Sendable {
    public let installed: Bool
    public let loaded: Bool
    public let launchConfiguration: GatewayLaunchConfiguration
    public let health: GatewayHealth?

    public var statusSummary: String {
        var parts = [
            installed ? "Installed" : "Not installed",
            loaded ? "Loaded" : "Not loaded",
        ]

        if let health {
            parts.append(health.displayStatus)
        } else {
            parts.append("Health unknown")
        }

        return parts.joined(separator: " / ")
    }
}

public struct GatewayLaunchConfiguration: Equatable, Sendable {
    public static let validPortRange = 1...65_535

    public let host: String
    public let port: Int
    public let configPath: String
    public let executablePath: String?

    public init(host: String, port: Int, configPath: String, executablePath: String? = nil) {
        self.host = host
        self.port = port
        self.configPath = configPath
        self.executablePath = executablePath
    }

    public func usesGateway(at expectedURL: URL) -> Bool {
        guard let executablePath, executablePath.hasPrefix("/") else { return false }
        return URL(fileURLWithPath: executablePath).standardizedFileURL
            == expectedURL.standardizedFileURL
    }

    public var bindDescription: String? {
        isNetworkExposedBind ? "Network-exposed bind: \(host):\(port)" : nil
    }

    public var openAIBaseURL: String {
        endpointURL(path: "/v1")?.absoluteString ?? Self.invalidEndpointDescription
    }

    public var localRouterURL: String {
        endpointURL()?.absoluteString ?? Self.invalidEndpointDescription
    }

    public var anthropicRootURL: String {
        localRouterURL
    }

    public var healthURLString: String {
        healthURL?.absoluteString ?? Self.invalidEndpointDescription
    }

    public var healthURL: URL? {
        endpointURL(path: "/healthz")
    }

    /// Health checks are local control-plane traffic. Never resolve a hostname or contact a remote
    /// address based on mutable LaunchAgent contents.
    var loopbackHealthURL: URL? {
        guard ["127.0.0.1", "::1", "[::1]", "0.0.0.0", "::"].contains(host) else {
            return nil
        }
        return endpointURL(path: "/healthz")
    }

    private func endpointURL(path: String = "") -> URL? {
        guard Self.validPortRange.contains(port) else { return nil }

        var components = URLComponents()
        components.scheme = "http"
        components.host = urlHost
        components.port = port
        components.path = path
        return components.url
    }

    private var pasteableHost: String {
        isNetworkExposedBind ? Self.localLoopbackHost : host
    }

    private var urlHost: String {
        let host = pasteableHost
        guard host.contains(":"), !host.hasPrefix("[") else { return host }
        return "[\(host)]"
    }

    private var isNetworkExposedBind: Bool {
        host == "0.0.0.0" || host == "::"
    }

    private static let localLoopbackHost = "127.0.0.1"
    private static let invalidEndpointDescription = "Invalid gateway address"
}

public struct GatewayHealth: Decodable, Equatable, Sendable {
    public static let builtInRouteNames = ["auto", "prefer-local", "prefer-hosted"]

    public let status: String
    public let models: [String]
    public let offline: Bool
    public let missingKeys: [String]

    public init(status: String, models: [String], offline: Bool, missingKeys: [String] = []) {
        self.status = status
        self.models = models
        self.offline = offline
        self.missingKeys = missingKeys
    }

    public var displayStatus: String {
        if offline { return "Offline" }
        if status == "ok" { return "Healthy" }
        if status == "degraded" { return "Degraded" }
        return status.capitalized
    }

    public var detailSummary: String {
        var details: [String] = []
        details.append(models.isEmpty ? "No models configured" : "\(models.count) model\(models.count == 1 ? "" : "s")")
        if offline {
            details.append("offline mode")
        }
        if !missingKeys.isEmpty {
            details.append("missing keys: \(missingKeys.joined(separator: ", "))")
        }
        return details.joined(separator: " / ")
    }

    public var availableRouteNames: [String] {
        var seen = Set<String>()
        return (Self.builtInRouteNames + models).filter { name in
            seen.insert(name).inserted
        }
    }

    private enum CodingKeys: String, CodingKey {
        case status
        case models
        case offline
        case missingKeys = "missing_keys"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decode(String.self, forKey: .status)
        models = try container.decodeIfPresent([String].self, forKey: .models) ?? []
        offline = try container.decodeIfPresent(Bool.self, forKey: .offline) ?? false
        missingKeys = try container.decodeIfPresent([String].self, forKey: .missingKeys) ?? []
    }
}

public enum GatewayServiceControllerError: LocalizedError, Sendable {
    case restartFailed(String)
    case serviceNeedsRepair

    public var errorDescription: String? {
        switch self {
        case .restartFailed(let message):
            return message.isEmpty ? "Could not restart the gateway service." : message
        case .serviceNeedsRepair:
            return "The installed gateway service does not use this copy of Wayfinder. Run Setup to repair it."
        }
    }
}

private struct ProcessResult: Sendable {
    let exitCode: Int32
    let stderr: String

    var isSuccess: Bool {
        exitCode == 0
    }
}
