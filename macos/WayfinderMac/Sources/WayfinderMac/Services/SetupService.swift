import Foundation

public struct SetupProcessResult: Sendable {
    public let exitCode: Int32
    public let stderr: String
    public var succeeded: Bool { exitCode == 0 }
}

public struct SetupService: Sendable {
    public typealias Runner = @Sendable (SetupCommand) async -> SetupProcessResult

    private let resolver: GatewayToolResolver
    private let service: GatewayServiceController
    private let keychain: KeychainCredentialStore
    private let fileExists: @Sendable (String) -> Bool
    private let runner: Runner

    public init(
        resolver: GatewayToolResolver = GatewayToolResolver(),
        service: GatewayServiceController = GatewayServiceController(),
        keychain: KeychainCredentialStore = KeychainCredentialStore(),
        fileExists: @escaping @Sendable (String) -> Bool = { FileManager.default.fileExists(atPath: $0) },
        runner: @escaping Runner = { command in await SetupService.run(command) }
    ) {
        self.resolver = resolver
        self.service = service
        self.keychain = keychain
        self.fileExists = fileExists
        self.runner = runner
    }

    public func assess(previouslyHealthy: Bool = UserDefaults.standard.bool(forKey: "Wayfinder.Setup.PreviouslyHealthy")) async -> SetupAssessment {
        guard resolver.resolve() != nil else { return .toolsMissing }
        let configExists = fileExists(GatewayServiceController.defaultConfigPath())
        let status = await service.status()
        if !configExists { return .neverConfigured }
        if !status.installed { return .existingConfig }
        if !status.loaded { return .stopped }
        guard let health = status.health else { return previouslyHealthy ? .unreachableAfterSuccess : .existingConfig }
        if !health.missingKeys.isEmpty { return .missingKeys(health.missingKeys) }
        if health.status == "ok" || health.offline {
            UserDefaults.standard.set(true, forKey: "Wayfinder.Setup.PreviouslyHealthy")
            return .healthy
        }
        return .unreachableAfterSuccess
    }

    public func run(
        preset: SetupPreset,
        credentials: [String: String],
        progress: @escaping @MainActor @Sendable (SetupProgressStage) async -> Void
    ) async throws -> SetupResult {
        guard let tool = resolver.resolve() else { throw SetupFailure.toolMissing }
        let configPath = GatewayServiceController.defaultConfigPath()
        if fileExists(configPath) { throw SetupFailure.existingConfiguration }
        let commands = try SetupCommandPlan.make(tool: tool, presetID: preset.id, configPath: configPath)

        try Task.checkCancellation()
        await progress(.creatingConfiguration)
        try await execute(commands[0], stage: .creatingConfiguration)

        try Task.checkCancellation()
        await progress(.updatingService)
        _ = await runner(commands[1]) // uninstall is intentionally best effort
        try await execute(commands[2], stage: .updatingService)

        try Task.checkCancellation()
        await progress(.savingCredentials)
        let allowed = Set(preset.credentials.map(\.environmentVariable))
        guard Set(credentials.keys).isSubset(of: allowed) else { throw SetupFailure.invalidCredentialIdentifier }
        for credential in preset.credentials {
            guard let value = credentials[credential.environmentVariable], !value.isEmpty else { continue }
            try await keychain.store(envVar: credential.environmentVariable, key: value)
        }

        try Task.checkCancellation()
        await progress(.restartingGateway)
        do { try await service.restart() }
        catch { throw SetupFailure.commandFailed(stage: .restartingGateway, message: Self.sanitize(error.localizedDescription, secrets: credentials.values)) }

        try Task.checkCancellation()
        await progress(.checkingConfiguration)
        for delay in [200_000_000, 400_000_000, 800_000_000, 1_600_000_000] as [UInt64] {
            let status = await service.status()
            if let health = status.health {
                UserDefaults.standard.set(true, forKey: "Wayfinder.Setup.PreviouslyHealthy")
                return SetupResult(
                    presetID: preset.id,
                    gatewayAddress: status.launchConfiguration.localRouterURL,
                    endpointCount: health.models.count,
                    missingKeys: health.missingKeys
                )
            }
            try await Task.sleep(nanoseconds: delay)
        }
        throw SetupFailure.verificationTimedOut
    }

    private func execute(_ command: SetupCommand, stage: SetupProgressStage) async throws {
        let result = await runner(command)
        guard result.succeeded else {
            throw SetupFailure.commandFailed(stage: stage, message: Self.sanitize(result.stderr, secrets: []))
        }
    }

    static func sanitize<S: Sequence>(_ message: String, secrets: S) -> String where S.Element == String {
        secrets.reduce(message) { text, secret in
            secret.isEmpty ? text : text.replacingOccurrences(of: secret, with: "[redacted]")
        }.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func run(_ command: SetupCommand) async -> SetupProcessResult {
        await Task.detached {
            let process = Process()
            process.executableURL = command.executable
            process.arguments = command.arguments
            let errorPipe = Pipe()
            process.standardOutput = FileHandle(forWritingAtPath: "/dev/null")
            process.standardError = errorPipe
            do { try process.run() }
            catch { return SetupProcessResult(exitCode: 1, stderr: error.localizedDescription) }
            process.waitUntilExit()
            let data = errorPipe.fileHandleForReading.readDataToEndOfFile()
            return SetupProcessResult(exitCode: process.terminationStatus, stderr: String(data: data, encoding: .utf8) ?? "")
        }.value
    }
}
