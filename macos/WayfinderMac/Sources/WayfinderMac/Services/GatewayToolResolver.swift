import Foundation

public struct GatewayToolResolver: Sendable {
    private let environment: [String: String]
    private let isExecutable: @Sendable (String) -> Bool

    public init(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        isExecutable: @escaping @Sendable (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) }
    ) {
        self.environment = environment
        self.isExecutable = isExecutable
    }

    public func resolve(_ name: String = "wayfinder-router") -> URL? {
        if name == "wayfinder-router" {
            let bundled = Bundle.main.bundleURL
                .appendingPathComponent("Contents/Helpers/wayfinder-router")
            if isExecutable(bundled.path) { return bundled }
        }
        let inherited = environment["PATH", default: ""].split(separator: ":").map(String.init)
        let directories = inherited + ["/opt/homebrew/bin", "/usr/local/bin"]
        for directory in directories {
            let path = URL(fileURLWithPath: directory).appendingPathComponent(name).path
            if isExecutable(path) { return URL(fileURLWithPath: path) }
        }
        return nil
    }

    public func resolvesRuntime(_ executable: String) -> Bool {
        resolve(executable) != nil
    }
}

public struct SetupCommand: Equatable, Sendable {
    public let executable: URL
    public let arguments: [String]
}

public enum SetupCommandPlan {
    public static func make(tool: URL, presetID: String, configPath: String) throws -> [SetupCommand] {
        guard SetupPreset.approved.contains(where: { $0.id == presetID }) else { throw SetupFailure.invalidPreset }
        guard FileManager.default.isExecutableFile(atPath: tool.path) else { throw SetupFailure.toolMissing }
        let allowedRoot = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Wayfinder").standardizedFileURL.path
        let target = URL(fileURLWithPath: configPath).standardizedFileURL.path
        guard target.hasPrefix(allowedRoot + "/") else { throw SetupFailure.unsafeConfigPath }
        return [
            SetupCommand(executable: tool, arguments: ["init", "--preset", presetID, "--keychain", "--path", target]),
            SetupCommand(executable: tool, arguments: ["service", "uninstall"]),
            SetupCommand(executable: tool, arguments: ["service", "install", "--config", target]),
        ]
    }
}
