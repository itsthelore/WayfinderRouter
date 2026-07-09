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

    public func status() async -> GatewayServiceStatus {
        let launchConfiguration = readLaunchConfiguration()
        let loaded = await isLoaded()
        let health = await healthProbe(launchConfiguration.healthURL)
        return GatewayServiceStatus(
            installed: FileManager.default.fileExists(atPath: launchAgentURL.path),
            loaded: loaded,
            launchConfiguration: launchConfiguration,
            health: health
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
        let host = argumentValue(after: "--host", in: arguments) ?? defaultHost
        let port = Int(argumentValue(after: "--port", in: arguments) ?? "") ?? defaultPort
        let configPath = argumentValue(after: "--config", in: arguments) ?? defaultConfigPath()
        return GatewayLaunchConfiguration(host: host, port: port, configPath: configPath)
    }

    public static func decodeHealth(_ data: Data) throws -> GatewayHealth {
        try JSONDecoder().decode(GatewayHealth.self, from: data)
    }

    private func readLaunchConfiguration() -> GatewayLaunchConfiguration {
        guard
            let data = try? Data(contentsOf: launchAgentURL),
            let plist = try? PropertyListSerialization.propertyList(
                from: data,
                options: [],
                format: nil
            ) as? [String: Any],
            let arguments = plist["ProgramArguments"] as? [String]
        else {
            return GatewayLaunchConfiguration(
                host: Self.defaultHost,
                port: Self.defaultPort,
                configPath: Self.defaultConfigPath()
            )
        }

        return Self.launchConfiguration(fromProgramArguments: arguments)
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
    public let host: String
    public let port: Int
    public let configPath: String

    public var bindDescription: String? {
        isNetworkExposedBind ? "Network-exposed bind: \(host):\(port)" : nil
    }

    public var openAIBaseURL: String {
        "\(rootURLString)/v1"
    }

    public var localRouterURL: String {
        rootURLString
    }

    public var anthropicRootURL: String {
        rootURLString
    }

    public var healthURLString: String {
        "\(rootURLString)/healthz"
    }

    public var healthURL: URL {
        URL(string: healthURLString)!
    }

    private var rootURLString: String {
        "http://\(pasteableHost):\(port)"
    }

    private var pasteableHost: String {
        isNetworkExposedBind ? Self.localLoopbackHost : host
    }

    private var isNetworkExposedBind: Bool {
        host == "0.0.0.0" || host == "::"
    }

    private static let localLoopbackHost = "127.0.0.1"
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

    public var errorDescription: String? {
        switch self {
        case .restartFailed(let message):
            return message.isEmpty ? "Could not restart the gateway service." : message
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
