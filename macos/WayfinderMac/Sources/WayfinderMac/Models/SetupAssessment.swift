import Foundation

public enum SetupStep: String, CaseIterable, Sendable {
    case checking, bundledHelperInvalid, serviceRepair, welcome, existingConfiguration, chooseRouting
    case requirements, credentials, configure, result
}

public enum SetupAssessment: Equatable, Sendable {
    case checking
    case bundledHelperInvalid
    case serviceNeedsRepair
    case neverConfigured
    case existingConfig
    case stopped
    case unreachableAfterSuccess
    case missingKeys([String])
    case healthy

    public var isIncomplete: Bool {
        switch self {
        case .bundledHelperInvalid, .serviceNeedsRepair, .neverConfigured, .existingConfig, .missingKeys: true
        case .checking, .stopped, .unreachableAfterSuccess, .healthy: false
        }
    }

    public var initialStep: SetupStep {
        switch self {
        case .checking: .checking
        case .bundledHelperInvalid: .bundledHelperInvalid
        case .serviceNeedsRepair: .serviceRepair
        case .neverConfigured: .welcome
        case .existingConfig: .existingConfiguration
        case .missingKeys: .credentials
        case .stopped, .unreachableAfterSuccess, .healthy: .result
        }
    }
}

public struct SetupCredential: Equatable, Sendable {
    public let provider: String
    public let environmentVariable: String

    public init(provider: String, environmentVariable: String) {
        self.provider = provider
        self.environmentVariable = environmentVariable
    }
}

public struct SetupPreset: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let summary: String
    public let requirement: String
    public let credentials: [SetupCredential]
    public let localRuntimeExecutable: String?

    public static let appleLocal = SetupPreset(
        id: "apple-local",
        title: "Apple Foundation Models",
        summary: "Use Apple's on-device system model for local routing.",
        requirement: "Available on eligible Apple Silicon Macs when the model is ready.",
        credentials: [],
        localRuntimeExecutable: nil
    )

    public static let approved: [SetupPreset] = [
        .init(id: "hybrid", title: "Hybrid — Recommended", summary: "Use a local endpoint with a hosted fallback.", requirement: "Requires Ollama and an OpenAI key.", credentials: [.init(provider: "OpenAI", environmentVariable: "OPENAI_API_KEY")], localRuntimeExecutable: "ollama"),
        .init(id: "local", title: "Local only", summary: "Keep delivery local when offline operation is enforced.", requirement: "Requires Ollama; no provider key.", credentials: [], localRuntimeExecutable: "ollama"),
        .init(id: "openai", title: "OpenAI", summary: "Route across hosted OpenAI cost and capability tiers.", requirement: "Requires an OpenAI key.", credentials: [.init(provider: "OpenAI", environmentVariable: "OPENAI_API_KEY")], localRuntimeExecutable: nil),
        .init(id: "gemini", title: "Gemini", summary: "Route across hosted Gemini cost and capability tiers.", requirement: "Requires a Gemini key.", credentials: [.init(provider: "Google Gemini", environmentVariable: "GEMINI_API_KEY")], localRuntimeExecutable: nil),
    ]

    public static func approved(appleAvailability: AppleFoundationModelsAvailability) -> [SetupPreset] {
        appleAvailability == .available ? [appleLocal] + approved : approved
    }

    public static let commandPresetIDs = Set(([appleLocal] + approved).map(\.id))
}

public extension AppleFoundationModelsAvailability {
    var setupGuidance: String? {
        switch self {
        case .available:
            "Apple Foundation Models is available, so new setup can use the on-device local preset."
        case .deviceNotEligible:
            "Apple Foundation Models is not available on this device. Ollama and manual local setup remain available."
        case .appleIntelligenceNotEnabled:
            "Apple Intelligence is not enabled. Enable it in System Settings to use Apple Foundation Models, or continue with Ollama/manual local setup."
        case .modelNotReady:
            "Apple Foundation Models is still preparing. This is temporary; Ollama/manual local setup remains available."
        case .unsupported:
            "Apple Foundation Models requires a supported macOS version. Ollama/manual local setup remains available."
        case .unavailable:
            "Apple Foundation Models availability could not be confirmed. Ollama/manual local setup remains available."
        }
    }
}

public enum SetupProgressStage: Int, CaseIterable, Sendable {
    case creatingConfiguration, updatingService, savingCredentials, restartingGateway, checkingConfiguration

    public var title: String {
        switch self {
        case .creatingConfiguration: "Creating routing configuration"
        case .updatingService: "Updating the gateway service"
        case .savingCredentials: "Saving credentials"
        case .restartingGateway: "Restarting the gateway"
        case .checkingConfiguration: "Checking configuration"
        }
    }
}

public struct SetupResult: Equatable, Sendable {
    public let presetID: String
    public let gatewayAddress: String
    public let endpointCount: Int
    public let missingKeys: [String]

    public var isDegraded: Bool { !missingKeys.isEmpty || endpointCount == 0 }
}

public enum SetupFailure: LocalizedError, Equatable, Sendable {
    case bundledHelperInvalid, configurationMissing, existingConfiguration, invalidPreset, unsafeConfigPath, invalidCredentialIdentifier
    case commandFailed(stage: SetupProgressStage, message: String)
    case verificationTimedOut

    public var errorDescription: String? {
        switch self {
        case .bundledHelperInvalid: "Wayfinder could not verify its bundled gateway. Reinstall Wayfinder from an official release."
        case .configurationMissing: "Wayfinder could not find the existing gateway configuration to preserve."
        case .existingConfiguration: "A configuration already exists at this location."
        case .invalidPreset: "That routing preset is not supported."
        case .unsafeConfigPath: "The configuration path is outside Application Support."
        case .invalidCredentialIdentifier: "The setup requested an unsupported credential."
        case .commandFailed(let stage, let message): message.isEmpty ? "\(stage.title) failed." : message
        case .verificationTimedOut: "The gateway did not become ready before the check timed out."
        }
    }
}
