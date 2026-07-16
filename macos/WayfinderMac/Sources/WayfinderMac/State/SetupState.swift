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
    @Published public private(set) var appleAvailability: AppleFoundationModelsAvailability = .unsupported

    private let service: SetupService
    private let resolver: GatewayToolResolver
    private var operation: Task<Void, Never>?
    private var isAssessing = false

    public init(service: SetupService = SetupService(), resolver: GatewayToolResolver = GatewayToolResolver()) {
        self.service = service
        self.resolver = resolver
    }

    public var approvedPresets: [SetupPreset] { SetupPreset.approved(appleAvailability: appleAvailability) }
    public var selectedPreset: SetupPreset { approvedPresets.first { $0.id == selectedPresetID } ?? approvedPresets[0] }
    public var requiredCredentials: [SetupCredential] { selectedPreset.credentials }

    public func assess() async {
        guard !isAssessing else { return }
        isAssessing = true
        defer { isAssessing = false }
        assessment = .checking; step = .checking; failureMessage = nil
        async let assessed = service.assess()
        async let apple = service.appleFoundationModelsAvailability()
        let (value, availability) = await (assessed, apple)
        appleAvailability = availability
        selectedPresetID = Self.selectedPresetID(
            afterAssessment: value,
            appleAvailability: availability,
            current: selectedPresetID
        )
        assessment = value; step = value.initialStep
    }

    nonisolated public static func selectedPresetID(
        afterAssessment assessment: SetupAssessment,
        appleAvailability: AppleFoundationModelsAvailability,
        current: String
    ) -> String {
        if assessment == .neverConfigured {
            return appleAvailability == .available ? SetupPreset.appleLocal.id : "hybrid"
        }
        if current == SetupPreset.appleLocal.id, appleAvailability != .available {
            return "hybrid"
        }
        return current
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
