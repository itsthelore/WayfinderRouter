import Foundation

public protocol ChatGPTProviderConfigurator: Sendable {
    func configure() async throws
}

public struct LocalChatGPTProviderConfigurator: ChatGPTProviderConfigurator {
    private let resolver: GatewayToolResolver
    private let service: GatewayServiceController

    public init(
        resolver: GatewayToolResolver = GatewayToolResolver(),
        service: GatewayServiceController = GatewayServiceController()
    ) {
        self.resolver = resolver
        self.service = service
    }

    public func configure() async throws {
        let tool: URL
        do {
            tool = try await resolver.resolveGateway()
        } catch {
            throw ChatGPTProviderSetupError.gatewayMissing
        }
        let path = GatewayServiceController.defaultConfigPath()
        let result = await Self.run(tool, ["app-configure-chatgpt", "--path", path])
        guard result.exitCode == 0 else {
            throw ChatGPTProviderSetupError.configurationFailed(result.stderr)
        }
        try await service.restart()
        for _ in 0..<20 {
            if (await service.status()).health != nil { return }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        throw ChatGPTProviderSetupError.gatewayDidNotRestart
    }

    private static func run(_ executable: URL, _ arguments: [String]) async -> ChatGPTSetupProcessResult {
        await Task.detached {
            let process = Process()
            process.executableURL = executable
            process.arguments = arguments
            let stderr = Pipe()
            process.standardOutput = FileHandle(forWritingAtPath: "/dev/null")
            process.standardError = stderr
            do { try process.run() } catch {
                return ChatGPTSetupProcessResult(exitCode: 1, stderr: error.localizedDescription)
            }
            process.waitUntilExit()
            let data = stderr.fileHandleForReading.readDataToEndOfFile()
            return ChatGPTSetupProcessResult(
                exitCode: process.terminationStatus,
                stderr: String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            )
        }.value
    }
}

private struct ChatGPTSetupProcessResult: Sendable {
    let exitCode: Int32
    let stderr: String
}

public enum ChatGPTProviderSetupError: LocalizedError, Sendable {
    case gatewayMissing
    case configurationFailed(String)
    case gatewayDidNotRestart

    public var errorDescription: String? {
        switch self {
        case .gatewayMissing:
            return "Wayfinder could not find its bundled gateway. Reinstall the app and try again."
        case .configurationFailed(let detail):
            return detail.isEmpty ? "Wayfinder could not add the ChatGPT destination." : detail
        case .gatewayDidNotRestart:
            return "ChatGPT was added, but the local gateway did not restart. Open Gateway settings and try again."
        }
    }
}
