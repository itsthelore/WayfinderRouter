import SwiftUI

public struct ProviderCredentialDetail: Equatable, Sendable {
    public let displayName: String
    public let providerName: String
    public let baseURL: String
    public let models: [String]
    public let keyEnvironmentVariable: String?
    public let symbolName: String

    public var isKeyless: Bool {
        keyEnvironmentVariable == nil
    }

    public var modelSummary: String {
        if models.count == 1 {
            return models[0]
        }
        return "\(models.count) models"
    }
}

public enum CredentialStatus: Equatable, Sendable {
    case unknown
    case keyMissing
    case keyPresent
    case local
    case comingSoon

    public var title: String {
        switch self {
        case .unknown:
            return "Checking"
        case .keyMissing:
            return "Key missing"
        case .keyPresent:
            return "Key present"
        case .local:
            return "Local"
        case .comingSoon:
            return "Coming Soon"
        }
    }

    public var symbolName: String {
        switch self {
        case .unknown:
            return "clock"
        case .keyMissing:
            return "exclamationmark.triangle"
        case .keyPresent:
            return "checkmark.circle"
        case .local:
            return "desktopcomputer"
        case .comingSoon:
            return "clock"
        }
    }

    public var tint: Color {
        switch self {
        case .unknown, .comingSoon:
            return .secondary
        case .keyMissing:
            return WayfinderTheme.cloud
        case .keyPresent, .local:
            return WayfinderTheme.local
        }
    }
}

public extension ProviderKind {
    var credentialDetail: ProviderCredentialDetail {
        switch self {
        case .anthropic:
            return ProviderCredentialDetail(
                displayName: "Anthropic",
                providerName: "anthropic",
                baseURL: "https://api.anthropic.com/v1",
                models: [
                    "claude-sonnet-4-6",
                    "claude-opus-4-8",
                    "claude-haiku",
                ],
                keyEnvironmentVariable: "ANTHROPIC_API_KEY",
                symbolName: "cloud"
            )
        case .openAI:
            return ProviderCredentialDetail(
                displayName: "OpenAI",
                providerName: "openai",
                baseURL: "https://api.openai.com/v1",
                models: [
                    "gpt-4o-mini",
                    "gpt-4o",
                ],
                keyEnvironmentVariable: "OPENAI_API_KEY",
                symbolName: "cloud"
            )
        case .googleGemini:
            return ProviderCredentialDetail(
                displayName: "Google Gemini",
                providerName: "gemini",
                baseURL: "https://generativelanguage.googleapis.com/v1beta/openai",
                models: [
                    "gemini-2.5-flash",
                    "gemini-2.5-pro",
                ],
                keyEnvironmentVariable: "GEMINI_API_KEY",
                symbolName: "cloud"
            )
        case .ollama:
            return ProviderCredentialDetail(
                displayName: "Ollama",
                providerName: "ollama",
                baseURL: "http://127.0.0.1:11434/v1",
                models: ["llama3.1"],
                keyEnvironmentVariable: nil,
                symbolName: "desktopcomputer"
            )
        case .lmStudio:
            return ProviderCredentialDetail(
                displayName: "LM Studio",
                providerName: "lmstudio",
                baseURL: "http://127.0.0.1:1234/v1",
                models: ["local-model"],
                keyEnvironmentVariable: nil,
                symbolName: "desktopcomputer"
            )
        case .custom:
            return ProviderCredentialDetail(
                displayName: "Custom",
                providerName: "custom",
                baseURL: "https://example.com/v1",
                models: ["model-name"],
                keyEnvironmentVariable: "PROVIDER_API_KEY",
                symbolName: "wrench.and.screwdriver"
            )
        }
    }
}
