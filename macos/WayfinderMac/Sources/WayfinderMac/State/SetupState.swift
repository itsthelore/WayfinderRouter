import Foundation

@MainActor
public final class SetupState: ObservableObject {
    @Published public private(set) var assessment: SetupAssessment = .checking
    @Published public var step: SetupStep = .checking
    @Published public var selectedPresetID = "hybrid"
    @Published public private(set) var progressStage: SetupProgressStage?
    @Published public private(set) var result: SetupResult?
    @Published public private(set) var failureMessage: String?
    @Published public private(set) var isMutating = false
    @Published public private(set) var missingRuntime: String?

    private let service: SetupService
    private let resolver: GatewayToolResolver
    private var operation: Task<Void, Never>?

    public init(service: SetupService = SetupService(), resolver: GatewayToolResolver = GatewayToolResolver()) {
        self.service = service
        self.resolver = resolver
    }

    public var selectedPreset: SetupPreset { SetupPreset.approved.first { $0.id == selectedPresetID } ?? SetupPreset.approved[0] }
    public var requiredCredentials: [SetupCredential] { selectedPreset.credentials }

    public func assess() async {
        assessment = .checking; step = .checking; failureMessage = nil
        let value = await service.assess()
        assessment = value; step = value.initialStep
    }

    public func continueFromWelcome() { step = .chooseRouting }
    public func chooseRouting() {
        missingRuntime = selectedPreset.localRuntimeExecutable.flatMap { resolver.resolvesRuntime($0) ? nil : $0 }
        if missingRuntime != nil { step = .requirements }
        else { step = requiredCredentials.isEmpty ? .configure : .credentials }
    }
    public func requirementsChecked() { chooseRouting() }
    public func credentialsReady() { step = .configure }
    public func back() {
        switch step {
        case .chooseRouting: step = .welcome
        case .requirements, .credentials: step = .chooseRouting
        case .configure: step = requiredCredentials.isEmpty ? .chooseRouting : .credentials
        default: break
        }
    }

    public func configure(credentials: [String: String], completion: @escaping @MainActor () -> Void) {
        guard !isMutating else { return }
        isMutating = true; failureMessage = nil
        let preset = selectedPreset
        operation = Task {
            do {
                let value = try await service.run(preset: preset, credentials: credentials) { [weak self] stage in
                    self?.progressStage = stage
                }
                result = value; step = .result; isMutating = false; completion()
            } catch is CancellationError {
                isMutating = false; await assess()
            } catch {
                failureMessage = error.localizedDescription; isMutating = false
            }
        }
    }

    public func cancel() { operation?.cancel() }
}

public extension Notification.Name {
    static let wayfinderRunSetupAssistant = Notification.Name("Wayfinder.RunSetupAssistant")
    static let wayfinderSetupDidChange = Notification.Name("Wayfinder.SetupDidChange")
}
