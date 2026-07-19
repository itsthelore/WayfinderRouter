import Foundation

public struct SetupProcessResult: Sendable {
    public let exitCode: Int32
    public let stderr: String
    public var succeeded: Bool { exitCode == 0 }
}

public struct SetupService: Sendable {
    public typealias Runner = @Sendable (SetupCommand) async -> SetupProcessResult
    public typealias ServiceStatusQuery = @Sendable (_ expectedGateway: URL) async -> GatewayServiceStatus
    public typealias ServiceRestart = @Sendable (_ expectedGateway: URL) async throws -> Void
    public typealias AppleAvailabilityQuery = @Sendable () async -> AppleFoundationModelsAvailability
    public typealias AppleProductReadinessQuery = @Sendable () -> Bool

    private let resolver: GatewayToolResolver
    private let keychain: KeychainCredentialStore
    private let fileExists: @Sendable (String) -> Bool
    private let runner: Runner
    private let serviceStatusQuery: ServiceStatusQuery
    private let serviceRestart: ServiceRestart
    private let appleAvailabilityQuery: AppleAvailabilityQuery
    private let appleProductReadinessQuery: AppleProductReadinessQuery

    public init(
        resolver: GatewayToolResolver = GatewayToolResolver(),
        service: GatewayServiceController = GatewayServiceController(),
        keychain: KeychainCredentialStore = KeychainCredentialStore(),
        fileExists: @escaping @Sendable (String) -> Bool = { FileManager.default.fileExists(atPath: $0) },
        runner: @escaping Runner = { command in await SetupService.run(command) },
        serviceStatusQuery: ServiceStatusQuery? = nil,
        serviceRestart: ServiceRestart? = nil,
        appleAvailabilityQuery: @escaping AppleAvailabilityQuery = {
            AppleFoundationModelsAvailabilityQuery.current()
        },
        appleProductReadinessQuery: @escaping AppleProductReadinessQuery = {
            AppleFoundationModelsProductReadiness.current().isReady
        }
    ) {
        self.resolver = resolver
        self.keychain = keychain
        self.fileExists = fileExists
        self.runner = runner
        self.serviceStatusQuery = serviceStatusQuery ?? { expectedGateway in
            await service.status(expectedGateway: expectedGateway)
        }
        self.serviceRestart = serviceRestart ?? { expectedGateway in
            try await service.restart(expectedGateway: expectedGateway)
        }
        self.appleAvailabilityQuery = appleAvailabilityQuery
        self.appleProductReadinessQuery = appleProductReadinessQuery
    }

    public func appleFoundationModelsAvailability() async -> AppleFoundationModelsAvailability {
        guard appleProductReadinessQuery() else { return .unsupported }
        return await appleAvailabilityQuery()
    }

    public func assess(previouslyHealthy: Bool = UserDefaults.standard.bool(forKey: "Wayfinder.Setup.PreviouslyHealthy")) async -> SetupAssessment {
        let tool: URL
        do {
            tool = try await resolver.resolveGateway()
        } catch {
            return .bundledHelperInvalid
        }
        let configExists = fileExists(GatewayServiceController.defaultConfigPath())
        let status = await serviceStatusQuery(tool)
        if configExists,
           status.installed,
           !status.launchConfiguration.usesGateway(at: tool) {
            return .serviceNeedsRepair
        }
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
        let tool: URL
        do {
            tool = try await resolver.resolveGateway()
        } catch {
            throw SetupFailure.bundledHelperInvalid
        }
        let configPath = GatewayServiceController.defaultConfigPath()
        if fileExists(configPath) { throw SetupFailure.existingConfiguration }
        let commands = try SetupCommandPlan.make(tool: tool, presetID: preset.id, configPath: configPath)

        try Task.checkCancellation()
        await progress(.creatingConfiguration)
        try await execute(commands[0], stage: .creatingConfiguration)

        try Task.checkCancellation()
        await progress(.updatingService)
        try await authenticate(commands[1])
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
        do { try await serviceRestart(tool) }
        catch { throw SetupFailure.commandFailed(stage: .restartingGateway, message: Self.sanitize(error.localizedDescription, secrets: credentials.values)) }

        try Task.checkCancellation()
        await progress(.checkingConfiguration)
        for delay in [200_000_000, 400_000_000, 800_000_000, 1_600_000_000] as [UInt64] {
            let status = await serviceStatusQuery(tool)
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

    /// Replaces only the LaunchAgent using the authenticated bundled gateway. The existing config
    /// and Keychain credentials are neither read nor rewritten by this operation.
    public func repairExistingService() async throws {
        let tool: URL
        do {
            tool = try await resolver.resolveGateway()
        } catch {
            throw SetupFailure.bundledHelperInvalid
        }
        let configPath = GatewayServiceController.defaultConfigPath()
        guard fileExists(configPath) else { throw SetupFailure.configurationMissing }

        let commands = Self.serviceRepairCommands(tool: tool, configPath: configPath)
        try await authenticate(commands[0])
        _ = await runner(commands[0]) // uninstall remains best effort, but is time-bounded
        try await execute(commands[1], stage: .updatingService)
    }

    static func serviceRepairCommands(tool: URL, configPath: String) -> [SetupCommand] {
        [
            SetupCommand(executable: tool, arguments: ["service", "uninstall"]),
            SetupCommand(
                executable: tool,
                arguments: ["service", "install", "--config", configPath]
            ),
        ]
    }

    private func execute(_ command: SetupCommand, stage: SetupProgressStage) async throws {
        try await authenticate(command)
        let result = await runner(command)
        guard result.succeeded else {
            throw SetupFailure.commandFailed(stage: stage, message: Self.sanitize(result.stderr, secrets: []))
        }
    }

    private func authenticate(_ command: SetupCommand) async throws {
        let verified: URL
        do {
            verified = try await resolver.resolveGateway()
        } catch {
            throw SetupFailure.bundledHelperInvalid
        }
        guard verified.standardizedFileURL == command.executable.standardizedFileURL else {
            throw SetupFailure.bundledHelperInvalid
        }
    }

    static func sanitize<S: Sequence>(_ message: String, secrets: S) -> String where S.Element == String {
        secrets.reduce(message) { text, secret in
            secret.isEmpty ? text : text.replacingOccurrences(of: secret, with: "[redacted]")
        }.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func run(_ command: SetupCommand) async -> SetupProcessResult {
        do {
            let result = try await BoundedProcessRunner.run(
                executable: command.executable,
                arguments: command.arguments,
                timeoutNanoseconds: 30_000_000_000,
                maximumInputBytes: 1,
                maximumOutputBytes: 64 * 1_024
            )
            return SetupProcessResult(
                exitCode: result.exitCode,
                stderr: String(data: result.stderr, encoding: .utf8) ?? ""
            )
        } catch {
            return SetupProcessResult(exitCode: 1, stderr: error.localizedDescription)
        }
    }
}
