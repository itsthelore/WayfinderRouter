import Foundation

public struct RoutingConfigStore: Sendable {
    public static var defaultConfigURL: URL {
        FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Wayfinder/wayfinder-router.toml")
    }

    public typealias CommandRunner = @Sendable (_ arguments: [String], _ stdin: String?) async -> RoutingCommandResult

    private let configURL: URL
    private let runner: CommandRunner

    public init(
        configURL: URL = Self.defaultConfigURL,
        runner: @escaping CommandRunner = { arguments, stdin in
            await RoutingConfigStore.runWayfinderRouter(arguments: arguments, stdin: stdin)
        }
    ) {
        self.configURL = configURL
        self.runner = runner
    }

    public func load() async throws -> RoutingSettingsState {
        guard FileManager.default.fileExists(atPath: configURL.path) else {
            throw RoutingConfigStoreError.missingConfig(configURL.path)
        }
        let result = await runner(["config", "read-routing", "--path", configURL.path], nil)
        guard result.isSuccess else {
            throw RoutingConfigStoreError.cli(result.stderr)
        }
        guard let data = result.stdout.data(using: .utf8) else {
            throw RoutingConfigStoreError.parse("Routing config output was not UTF-8.")
        }
        do {
            return try JSONDecoder().decode(RoutingConfigSnapshot.self, from: data).state
        } catch {
            throw RoutingConfigStoreError.parse(error.localizedDescription)
        }
    }

    public func save(_ state: RoutingSettingsState) async throws {
        guard FileManager.default.fileExists(atPath: configURL.path) else {
            throw RoutingConfigStoreError.missingConfig(configURL.path)
        }
        let existing: String
        do {
            existing = try String(contentsOf: configURL, encoding: .utf8)
        } catch {
            throw RoutingConfigStoreError.parse(error.localizedDescription)
        }
        if let field = Self.firstUnsupportedRoutingField(in: existing) {
            throw RoutingConfigStoreError.unsupportedConfig(field)
        }

        var draft = state
        draft.normalize()
        let result = await runner(
            ["config", "apply-routing", "--path", configURL.path],
            draft.routingTOML()
        )
        guard result.isSuccess else {
            throw RoutingConfigStoreError.cli(result.stderr)
        }
    }

    public static func runWayfinderRouter(arguments: [String], stdin: String?) async -> RoutingCommandResult {
        await Task.detached {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["wayfinder-router"] + arguments

            let stdout = Pipe()
            let stderr = Pipe()
            process.standardOutput = stdout
            process.standardError = stderr

            let input: Pipe?
            if stdin != nil {
                let pipe = Pipe()
                process.standardInput = pipe
                input = pipe
            } else {
                input = nil
            }

            do {
                try process.run()
                if let stdin, let data = stdin.data(using: .utf8), let input {
                    input.fileHandleForWriting.write(data)
                    try? input.fileHandleForWriting.close()
                }
            } catch {
                return RoutingCommandResult(
                    exitCode: 1,
                    stdout: "",
                    stderr: "wayfinder-router: \(error.localizedDescription)"
                )
            }

            process.waitUntilExit()
            let outputText = String(
                data: stdout.fileHandleForReading.readDataToEndOfFile(),
                encoding: .utf8
            ) ?? ""
            let errorText = String(
                data: stderr.fileHandleForReading.readDataToEndOfFile(),
                encoding: .utf8
            )?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return RoutingCommandResult(
                exitCode: process.terminationStatus,
                stdout: outputText,
                stderr: errorText
            )
        }.value
    }

    private static func firstUnsupportedRoutingField(in text: String) -> String? {
        var section = ""

        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = rawLine
                .split(separator: "#", maxSplits: 1, omittingEmptySubsequences: false)
                .first?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            guard !line.isEmpty else { continue }

            if line.hasPrefix("["), line.hasSuffix("]") {
                section = line.trimmingCharacters(in: CharacterSet(charactersIn: "[] "))
                if section.hasPrefix("routing."), section != "routing.tiers" {
                    return section
                }
                continue
            }

            guard section == "routing" || section == "routing.tiers",
                  let separator = line.firstIndex(of: "=") else {
                continue
            }
            let key = line[..<separator].trimmingCharacters(in: .whitespaces)
            let supported = section == "routing"
                ? Set(["threshold", "weights"])
                : Set(["min_score", "model"])
            if !supported.contains(key) {
                return "\(section).\(key)"
            }
        }

        return nil
    }
}

public struct RoutingCommandResult: Sendable {
    public let exitCode: Int32
    public let stdout: String
    public let stderr: String

    public init(exitCode: Int32, stdout: String = "", stderr: String = "") {
        self.exitCode = exitCode
        self.stdout = stdout
        self.stderr = stderr
    }

    public var isSuccess: Bool { exitCode == 0 }
}

public enum RoutingConfigStoreError: LocalizedError, Equatable, Sendable {
    case missingConfig(String)
    case parse(String)
    case cli(String)
    case unsupportedConfig(String)

    public var errorDescription: String? {
        switch self {
        case .missingConfig(let path):
            return "No router config found at \(path). Create one from Gateway settings or run setup first."
        case .parse(let message):
            return "Could not read routing config: \(message)"
        case .cli(let message):
            return message.isEmpty ? "The routing config command failed." : message
        case .unsupportedConfig(let field):
            return "Routing settings did not save because the config contains \(field), which this UI cannot preserve yet. Edit the config directly instead."
        }
    }
}

private struct RoutingConfigSnapshot: Decodable {
    let mode: RoutingSettingsMode
    let threshold: Double?
    let tiers: [SnapshotTier]?
    let weights: [SnapshotWeight]
    let models: [String]?

    var state: RoutingSettingsState {
        let tierRows = (tiers ?? []).enumerated().map { index, tier in
            RoutingTierRow(model: tier.model, minScore: tier.minScore, editable: index != 0)
        }
        var state = RoutingSettingsState(
            mode: mode,
            threshold: threshold ?? tierRows.dropFirst().first?.minScore ?? 0.5,
            tiers: tierRows.isEmpty ? RoutingSettingsState().tiers : tierRows,
            weights: weights.map(\.row),
            classifierModels: models ?? [],
            dirty: false,
            saving: false,
            error: nil
        )
        state.normalize()
        return state
    }
}

private struct SnapshotTier: Decodable {
    let minScore: Double
    let model: String

    private enum CodingKeys: String, CodingKey {
        case minScore = "min_score"
        case model
    }
}

private struct SnapshotWeight: Decodable {
    let id: String
    let label: String
    let value: Double
    let `default`: Double

    var row: RoutingWeightRow {
        RoutingWeightRow(id: id, displayLabel: label, value: value, defaultValue: `default`)
    }
}
