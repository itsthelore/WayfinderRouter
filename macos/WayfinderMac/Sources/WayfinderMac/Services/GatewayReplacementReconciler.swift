import Foundation

public enum GatewayReplacementReconciliation: Equatable, Sendable {
    case unchanged
    case restarted
    case deferred
}

/// Restarts a loaded bundled gateway once after its containing app is replaced.
///
/// launchd can keep the old executable image alive when an app is replaced at the same path. That
/// stale process can no longer authenticate newly signed nested XPC services. The bundled-helper
/// verifier and LaunchAgent path check remain the security boundary; this marker only avoids an
/// unnecessary restart on every ordinary app launch.
public struct GatewayReplacementReconciler: Sendable {
    public typealias GatewayResolver = @Sendable () async throws -> URL
    public typealias StatusQuery = @Sendable (_ expectedGateway: URL) async -> GatewayServiceStatus
    public typealias Restarter = @Sendable (_ expectedGateway: URL) async throws -> Void
    public typealias FingerprintProvider = @Sendable (_ gateway: URL) -> String?
    public typealias MarkerReader = @Sendable () -> String?
    public typealias MarkerWriter = @Sendable (_ fingerprint: String) async -> Void

    private static let markerKey = "Wayfinder.GatewayInstalledFingerprint"

    private let resolveGateway: GatewayResolver
    private let statusQuery: StatusQuery
    private let restart: Restarter
    private let fingerprintProvider: FingerprintProvider
    private let markerReader: MarkerReader
    private let markerWriter: MarkerWriter

    public init(
        resolver: GatewayToolResolver = GatewayToolResolver(),
        service: GatewayServiceController = GatewayServiceController()
    ) {
        self.resolveGateway = { try await resolver.resolveGateway() }
        self.statusQuery = { await service.status(expectedGateway: $0) }
        self.restart = { try await service.restart(expectedGateway: $0) }
        self.fingerprintProvider = { Self.installationFingerprint(gateway: $0) }
        self.markerReader = { UserDefaults.standard.string(forKey: Self.markerKey) }
        self.markerWriter = { UserDefaults.standard.set($0, forKey: Self.markerKey) }
    }

    init(
        resolveGateway: @escaping GatewayResolver,
        statusQuery: @escaping StatusQuery,
        restart: @escaping Restarter,
        fingerprintProvider: @escaping FingerprintProvider,
        markerReader: @escaping MarkerReader,
        markerWriter: @escaping MarkerWriter
    ) {
        self.resolveGateway = resolveGateway
        self.statusQuery = statusQuery
        self.restart = restart
        self.fingerprintProvider = fingerprintProvider
        self.markerReader = markerReader
        self.markerWriter = markerWriter
    }

    public func reconcile() async -> GatewayReplacementReconciliation {
        guard let gateway = try? await resolveGateway(),
              let fingerprint = fingerprintProvider(gateway) else {
            return .deferred
        }
        if markerReader() == fingerprint {
            return .unchanged
        }

        let status = await statusQuery(gateway)
        guard status.installed,
              status.loaded,
              status.launchConfiguration.usesGateway(at: gateway) else {
            return .deferred
        }

        do {
            try await restart(gateway)
            await markerWriter(fingerprint)
            return .restarted
        } catch {
            return .deferred
        }
    }

    static func installationFingerprint(
        appBundleURL: URL = Bundle.main.bundleURL,
        gateway: URL
    ) -> String? {
        guard let values = try? gateway.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey]),
              let fileSize = values.fileSize,
              let modified = values.contentModificationDate else {
            return nil
        }
        let info = Bundle(url: appBundleURL)?.infoDictionary ?? Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "unknown"
        let build = info["CFBundleVersion"] as? String ?? "unknown"
        let signing: String
        switch AppleFoundationModelsProductReadiness.current(appBundleURL: appBundleURL) {
        case .ready(let teamIdentifier):
            signing = teamIdentifier
        case .incompleteOrInvalid:
            signing = "untrusted"
        }
        let modifiedNanoseconds = Int64(modified.timeIntervalSince1970 * 1_000_000_000)
        return [version, build, signing, String(fileSize), String(modifiedNanoseconds)]
            .joined(separator: "|")
    }
}
