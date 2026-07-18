import Foundation

public enum CodexLoginFlow: String, Codable, CaseIterable, Sendable {
    case browser
    case deviceCode = "device-code"
}

public struct CodexAccountProfile: Equatable, Sendable {
    public let email: String?
    public let plan: String?

    public init(email: String?, plan: String?) {
        self.email = email
        self.plan = plan
    }
}

public struct CodexPendingLogin: Equatable, Sendable {
    public let id: String
    public let flow: CodexLoginFlow
    public let url: URL
    public let userCode: String?

    public init(id: String, flow: CodexLoginFlow, url: URL, userCode: String? = nil) {
        self.id = id
        self.flow = flow
        self.url = url
        self.userCode = userCode
    }
}

public enum CodexAccountSnapshot: Equatable, Sendable, Decodable {
    case signedOut
    case awaitingBrowser(CodexPendingLogin)
    case awaitingDeviceCode(CodexPendingLogin)
    case connected(CodexAccountProfile)
    case reauthenticationRequired(detail: String?)
    case unavailable(detail: String?)

    private enum Status: String, Decodable {
        case signedOut = "signed_out"
        case awaitingBrowser = "awaiting_browser"
        case awaitingDeviceCode = "awaiting_device_code"
        case connected
        case reauthenticationRequired = "reauth_required"
        case unavailable
    }

    private enum CodingKeys: String, CodingKey {
        case status
        case account
        case login
        case detail
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let status = try container.decode(Status.self, forKey: .status)

        switch status {
        case .signedOut:
            self = .signedOut
        case .awaitingBrowser:
            let login = try container.decode(LoginWire.self, forKey: .login).normalized()
            guard login.flow == .browser, login.userCode == nil else {
                throw Self.invalid("Browser login data did not match its status.")
            }
            self = .awaitingBrowser(login)
        case .awaitingDeviceCode:
            let login = try container.decode(LoginWire.self, forKey: .login).normalized()
            guard login.flow == .deviceCode, login.userCode?.isEmpty == false else {
                throw Self.invalid("Device-code login data did not match its status.")
            }
            self = .awaitingDeviceCode(login)
        case .connected:
            let account = try container.decode(AccountWire.self, forKey: .account)
            self = .connected(try account.normalized())
        case .reauthenticationRequired:
            self = .reauthenticationRequired(
                detail: try Self.optionalBoundedString(
                    container.decodeIfPresent(String.self, forKey: .detail),
                    maximumCharacters: 512,
                    field: "detail"
                )
            )
        case .unavailable:
            self = .unavailable(
                detail: try Self.optionalBoundedString(
                    container.decodeIfPresent(String.self, forKey: .detail),
                    maximumCharacters: 512,
                    field: "detail"
                )
            )
        }
    }

    private struct AccountWire: Decodable {
        let email: String?
        let plan: String?

        func normalized() throws -> CodexAccountProfile {
            CodexAccountProfile(
                email: try CodexAccountSnapshot.optionalBoundedString(
                    email,
                    maximumCharacters: 320,
                    field: "email"
                ),
                plan: try CodexAccountSnapshot.optionalBoundedString(
                    plan,
                    maximumCharacters: 128,
                    field: "plan"
                )
            )
        }
    }

    private struct LoginWire: Decodable {
        private enum Flow: String, Decodable {
            case browser
            case deviceCode = "device_code"

            var normalized: CodexLoginFlow {
                switch self {
                case .browser: return .browser
                case .deviceCode: return .deviceCode
                }
            }
        }

        let id: String
        private let flow: Flow
        let url: String
        let userCode: String?

        private enum CodingKeys: String, CodingKey {
            case id
            case flow
            case url
            case userCode = "user_code"
        }

        func normalized() throws -> CodexPendingLogin {
            let id = try CodexAccountSnapshot.requiredBoundedString(
                id,
                maximumCharacters: 128,
                field: "login id"
            )
            let urlText = try CodexAccountSnapshot.requiredBoundedString(
                url,
                maximumCharacters: 2_048,
                field: "login URL"
            )
            guard
                let components = URLComponents(string: urlText),
                components.scheme?.lowercased() == "https",
                let host = components.host?.lowercased(),
                CodexAccountSnapshot.isAllowedLoginHost(host),
                components.user == nil,
                components.password == nil,
                let url = components.url
            else {
                throw CodexAccountSnapshot.invalid("Login URL was not a safe HTTPS URL.")
            }
            return CodexPendingLogin(
                id: id,
                flow: flow.normalized,
                url: url,
                userCode: try CodexAccountSnapshot.optionalBoundedString(
                    userCode,
                    maximumCharacters: 64,
                    field: "user code"
                )
            )
        }
    }

    fileprivate static func requiredBoundedString(
        _ value: String,
        maximumCharacters: Int,
        field: String
    ) throws -> String {
        guard let value = try optionalBoundedString(
            value,
            maximumCharacters: maximumCharacters,
            field: field
        ) else {
            throw invalid("Missing \(field).")
        }
        return value
    }

    fileprivate static func optionalBoundedString(
        _ value: String?,
        maximumCharacters: Int,
        field: String
    ) throws -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard trimmed.count <= maximumCharacters,
              !trimmed.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
            throw invalid("Invalid \(field).")
        }
        return trimmed
    }

    fileprivate static func invalid(_ description: String) -> DecodingError {
        .dataCorrupted(.init(codingPath: [], debugDescription: description))
    }

    private static func isAllowedLoginHost(_ host: String) -> Bool {
        ["auth.openai.com", "chatgpt.com", "www.chatgpt.com"].contains(host)
    }
}

public extension CodexAccountSnapshot {
    var isConnected: Bool {
        if case .connected = self { return true }
        return false
    }
}

public struct CodexModel: Equatable, Sendable, Decodable, Identifiable {
    public let id: String
    public let displayName: String?

    public init(id: String, displayName: String? = nil) {
        self.id = id
        self.displayName = displayName
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try CodexAccountSnapshot.requiredBoundedString(
            container.decode(String.self, forKey: .id),
            maximumCharacters: 128,
            field: "model id"
        )
        displayName = try CodexAccountSnapshot.optionalBoundedString(
            container.decodeIfPresent(String.self, forKey: .displayName),
            maximumCharacters: 160,
            field: "model display name"
        )
    }

    public var label: String {
        displayName ?? id
    }
}

public struct CodexModelsResponse: Equatable, Sendable, Decodable {
    public static let maximumModelCount = 128

    public let models: [CodexModel]

    public init(models: [CodexModel]) {
        self.models = models
    }

    private enum CodingKeys: String, CodingKey {
        case models
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let decoded = try container.decode([CodexModel].self, forKey: .models)
        guard decoded.count <= Self.maximumModelCount else {
            throw DecodingError.dataCorruptedError(
                forKey: .models,
                in: container,
                debugDescription: "Model catalog exceeded its bound."
            )
        }
        guard Set(decoded.map(\.id)).count == decoded.count else {
            throw DecodingError.dataCorruptedError(
                forKey: .models,
                in: container,
                debugDescription: "Model catalog contained duplicate ids."
            )
        }
        models = decoded
    }
}
