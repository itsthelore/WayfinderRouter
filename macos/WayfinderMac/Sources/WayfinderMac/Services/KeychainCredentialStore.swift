import Foundation

public enum KeychainCredentialStoreError: LocalizedError, Sendable {
    case invalidEnvironmentVariable(String)
    case invalidKey
    case securityFailed(String)
    case missingStdin

    public var errorDescription: String? {
        switch self {
        case .invalidEnvironmentVariable(let envVar):
            return "Invalid environment variable name: \(envVar)"
        case .invalidKey:
            return "Keys must be non-empty printable ASCII, with no control characters."
        case .securityFailed(let message):
            return message.isEmpty ? "The macOS Keychain command failed." : message
        case .missingStdin:
            return "Could not write to the macOS Keychain command."
        }
    }
}

public struct KeychainCredentialStore: Sendable {
    public static let serviceName = "wayfinder-router"

    private let securityPath = "/usr/bin/security"

    public init() {}

    public func contains(envVar: String) async -> Bool {
        await runSecurity(arguments: [
            "find-generic-password",
            "-s", Self.serviceName,
            "-a", envVar,
        ]).isSuccess
    }

    public func store(envVar: String, key: String) async throws {
        let line = try keychainScript(operation: .add, envVar: envVar, key: key)
        try await runInteractiveSecurity(line: line)
    }

    public func delete(envVar: String) async throws {
        let line = try keychainScript(operation: .delete, envVar: envVar, key: "")
        try await runInteractiveSecurity(line: line)
    }

    private func keychainScript(operation: KeychainOperation, envVar: String, key: String) throws -> String {
        guard isValidEnvironmentVariable(envVar) else {
            throw KeychainCredentialStoreError.invalidEnvironmentVariable(envVar)
        }

        switch operation {
        case .add:
            guard isValidKey(key) else {
                throw KeychainCredentialStoreError.invalidKey
            }
            return """
            add-generic-password -U -s \(Self.serviceName) -a \(envVar) -T /usr/bin/security -w "\(escape(key))"
            """
        case .delete:
            return "delete-generic-password -s \(Self.serviceName) -a \(envVar)"
        }
    }

    private func runInteractiveSecurity(line: String) async throws {
        let result = await runSecurity(arguments: ["-i"], stdin: "\(line)\n")
        guard result.isSuccess else {
            throw KeychainCredentialStoreError.securityFailed(result.stderr)
        }
    }

    private func runSecurity(arguments: [String], stdin: String? = nil) async -> ProcessResult {
        await Task.detached {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: securityPath)
            process.arguments = arguments

            let stderr = Pipe()
            process.standardError = stderr

            if let stdin {
                let input = Pipe()
                process.standardInput = input
                do {
                    try process.run()
                    guard let data = stdin.data(using: .utf8) else {
                        return ProcessResult(exitCode: 1, stderr: "security: invalid stdin")
                    }
                    input.fileHandleForWriting.write(data)
                    input.fileHandleForWriting.closeFile()
                } catch {
                    return ProcessResult(exitCode: 1, stderr: "security: \(error.localizedDescription)")
                }
            } else {
                process.standardOutput = FileHandle(forWritingAtPath: "/dev/null")
                do {
                    try process.run()
                } catch {
                    return ProcessResult(exitCode: 1, stderr: "security: \(error.localizedDescription)")
                }
            }

            process.waitUntilExit()
            let errorData = stderr.fileHandleForReading.readDataToEndOfFile()
            let errorText = String(data: errorData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return ProcessResult(exitCode: process.terminationStatus, stderr: errorText)
        }.value
    }

    private func isValidEnvironmentVariable(_ name: String) -> Bool {
        guard let first = name.first, first.isASCII, first.isUppercase, name.count <= 64 else {
            return false
        }
        return name.allSatisfy { character in
            character == "_" || character.isNumber || (character.isASCII && character.isUppercase)
        }
    }

    private func isValidKey(_ key: String) -> Bool {
        !key.isEmpty && key.utf8.count <= 4096 && key.allSatisfy { character in
            character.isASCII && !character.isNewline && !character.isControl
        }
    }

    private func escape(_ value: String) -> String {
        value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
    }
}

private enum KeychainOperation {
    case add
    case delete
}

private struct ProcessResult: Sendable {
    let exitCode: Int32
    let stderr: String

    var isSuccess: Bool {
        exitCode == 0
    }
}

private extension Character {
    var isControl: Bool {
        unicodeScalars.allSatisfy { CharacterSet.controlCharacters.contains($0) }
    }
}
