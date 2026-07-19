import Foundation

public enum VerifiedGatewayRuntimeError: Error, Equatable, LocalizedError, Sendable {
    case bundledHelperInvalid
    case serviceNeedsRepair

    public var errorDescription: String? {
        switch self {
        case .bundledHelperInvalid:
            "Wayfinder could not verify its bundled gateway. Reinstall Wayfinder from an official release."
        case .serviceNeedsRepair:
            "The installed gateway service does not use this copy of Wayfinder. Open setup to repair it."
        }
    }
}

/// Gates loopback gateway access on both the bundled-helper handshake and the persisted LaunchAgent
/// executable path. It intentionally performs no network request itself.
public struct VerifiedGatewayRuntime: Sendable {
    public typealias LaunchConfigurationQuery = @Sendable () -> GatewayLaunchConfiguration?

    private let resolver: GatewayToolResolver
    private let launchConfigurationQuery: LaunchConfigurationQuery

    public init(
        resolver: GatewayToolResolver = GatewayToolResolver(),
        serviceController: GatewayServiceController = GatewayServiceController()
    ) {
        self.resolver = resolver
        self.launchConfigurationQuery = {
            serviceController.installedLaunchConfiguration()
        }
    }

    init(
        resolver: GatewayToolResolver,
        launchConfigurationQuery: @escaping LaunchConfigurationQuery
    ) {
        self.resolver = resolver
        self.launchConfigurationQuery = launchConfigurationQuery
    }

    public func validate() async throws {
        _ = try await validatedHelper()
    }

    public func validatedHelper() async throws -> URL {
        let helper: URL
        do {
            helper = try await resolver.resolveGateway()
        } catch {
            throw VerifiedGatewayRuntimeError.bundledHelperInvalid
        }
        guard launchConfigurationQuery()?.usesGateway(at: helper) == true else {
            throw VerifiedGatewayRuntimeError.serviceNeedsRepair
        }
        return helper
    }
}
