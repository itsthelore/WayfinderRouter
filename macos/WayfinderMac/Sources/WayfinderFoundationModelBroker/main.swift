import Foundation
import Security
import WayfinderMacCore

#if canImport(FoundationModels)
import FoundationModels
#endif

@objc private protocol FoundationModelBrokerProtocol {
    func availability(_ request: Data, withReply reply: @escaping (Data?, String?) -> Void)
    func generate(_ request: Data, withReply reply: @escaping (Data?, String?) -> Void)
    func stream(_ request: Data, to sink: FoundationModelStreamSink)
    func cancel(_ requestID: String, withReply reply: @escaping (String?) -> Void)
}

@objc private protocol FoundationModelStreamSink {
    func receive(_ event: Data)
    func finish(_ errorCode: String?)
}

private actor InferenceTasks {
    private var tasks: [String: Task<Void, Never>] = [:]
    private var pendingCancellations: Set<String> = []

    func insert(_ task: Task<Void, Never>, for requestID: String) {
        if pendingCancellations.remove(requestID) != nil {
            task.cancel()
            return
        }
        guard tasks.count < AppleFoundationModelsProtocolV1.maximumQueuedChunks else {
            task.cancel()
            return
        }
        tasks[requestID]?.cancel()
        tasks[requestID] = task
    }

    func remove(_ requestID: String) { tasks[requestID] = nil }
    func cancel(_ requestID: String) {
        if let task = tasks.removeValue(forKey: requestID) {
            task.cancel()
        } else if pendingCancellations.count < AppleFoundationModelsProtocolV1.maximumQueuedChunks {
            pendingCancellations.insert(requestID)
        }
    }
}

private final class FoundationModelBroker: NSObject, FoundationModelBrokerProtocol {
    private let tasks = InferenceTasks()

    func availability(_ request: Data, withReply reply: @escaping (Data?, String?) -> Void) {
        do {
            let decoded = try AppleFoundationModelsWireCodec.decode(
                AppleFoundationModelsAvailabilityRequest.self,
                from: request
            )
            try decoded.validate()
            let response = AppleFoundationModelsAvailabilityResponse(
                requestID: decoded.requestID,
                availability: AppleFoundationModelsAvailabilityQuery.current()
            )
            reply(try AppleFoundationModelsWireCodec.encode(response), nil)
        } catch {
            reply(nil, sanitizedCode(error))
        }
    }

    func generate(_ request: Data, withReply reply: @escaping (Data?, String?) -> Void) {
        do {
            let decoded = try decodeGenerate(request)
            let task = Task { [tasks] in
                defer { Task { await tasks.remove(decoded.requestID) } }
                do {
                    let content = try await generateContentWithTimeout(decoded)
                    try Task.checkCancellation()
                    let response = try AppleFoundationModelsGenerateResponse(
                        requestID: decoded.requestID,
                        content: content
                    )
                    reply(try AppleFoundationModelsWireCodec.encode(response), nil)
                } catch {
                    reply(nil, sanitizedCode(error))
                }
            }
            Task { await tasks.insert(task, for: decoded.requestID) }
        } catch {
            reply(nil, sanitizedCode(error))
        }
    }

    func stream(_ request: Data, to sink: FoundationModelStreamSink) {
        do {
            let decoded = try decodeGenerate(request)
            let task = Task { [tasks] in
                defer { Task { await tasks.remove(decoded.requestID) } }
                do {
                    let content = try await generateContentWithTimeout(decoded)
                    try Task.checkCancellation()
                    var sequence = 0
                    for chunk in boundedChunks(content) {
                        try Task.checkCancellation()
                        let event = try AppleFoundationModelsStreamEvent(
                            requestID: decoded.requestID,
                            sequence: sequence,
                            kind: .chunk,
                            content: chunk
                        )
                        sink.receive(try AppleFoundationModelsWireCodec.encode(event))
                        sequence += 1
                    }
                    try Task.checkCancellation()
                    let terminal = try AppleFoundationModelsStreamEvent(
                        requestID: decoded.requestID,
                        sequence: sequence,
                        kind: .terminal
                    )
                    sink.receive(try AppleFoundationModelsWireCodec.encode(terminal))
                    sink.finish(nil)
                } catch {
                    sink.finish(sanitizedCode(error))
                }
            }
            Task { await tasks.insert(task, for: decoded.requestID) }
        } catch {
            sink.finish(sanitizedCode(error))
        }
    }

    func cancel(_ requestID: String, withReply reply: @escaping (String?) -> Void) {
        guard !requestID.isEmpty,
              requestID.lengthOfBytes(using: .utf8) <= AppleFoundationModelsProtocolV1.maximumRequestIDBytes else {
            reply("invalid-request-id")
            return
        }
        Task { await tasks.cancel(requestID); reply(nil) }
    }

    private func decodeGenerate(_ data: Data) throws -> AppleFoundationModelsGenerateRequest {
        let request = try AppleFoundationModelsWireCodec.decode(
            AppleFoundationModelsGenerateRequest.self,
            from: data
        )
        try request.validate()
        return request
    }
}

private func generateContent(_ request: AppleFoundationModelsGenerateRequest) async throws -> String {
    #if canImport(FoundationModels)
    if #available(macOS 26.0, *) {
        guard AppleFoundationModelsAvailabilityQuery.current() == .available else {
            throw AppleFoundationModelsProtocolError.unavailable
        }
        let session = LanguageModelSession(instructions: request.instructions)
        let response = try await session.respond(to: request.normalizedPrompt)
        return response.content
    }
    #endif
    throw AppleFoundationModelsProtocolError.unavailable
}

private func generateContentWithTimeout(_ request: AppleFoundationModelsGenerateRequest) async throws -> String {
    try await withThrowingTaskGroup(of: String.self) { group in
        group.addTask { try await generateContent(request) }
        group.addTask {
            try await Task.sleep(for: .milliseconds(request.timeoutMilliseconds))
            throw AppleFoundationModelsProtocolError.timedOut
        }
        let result = try await group.next()!
        group.cancelAll()
        return result
    }
}

private func boundedChunks(_ content: String) -> [String] {
    var chunks: [String] = []
    var current = ""
    for scalar in content.unicodeScalars {
        let fragment = String(scalar)
        if current.lengthOfBytes(using: .utf8) + fragment.lengthOfBytes(using: .utf8)
            > AppleFoundationModelsProtocolV1.maximumChunkBytes {
            chunks.append(current)
            current = ""
        }
        current.append(fragment)
    }
    if !current.isEmpty { chunks.append(current) }
    return chunks
}

private func sanitizedCode(_ error: Error) -> String {
    switch error as? AppleFoundationModelsProtocolError {
    case .unsupportedVersion: return "unsupported-version"
    case .invalidRequestID: return "invalid-request-id"
    case .malformedPayload: return "malformed-payload"
    case .requestTooLarge: return "request-too-large"
    case .instructionsTooLarge, .tooManyMessages, .invalidMessage, .messageTooLarge: return "invalid-content"
    case .invalidTimeout: return "invalid-timeout"
    case .timedOut: return "timed-out"
    case .responseTooLarge, .chunkTooLarge, .queueFull: return "bound-exceeded"
    case .unauthorizedCaller: return "unauthorized"
    case .cancelled: return "cancelled"
    case .unavailable: return "unavailable"
    case nil: return error is CancellationError ? "cancelled" : "generation-failed"
    }
}

private final class BrokerDelegate: NSObject, NSXPCListenerDelegate {
    private let broker = FoundationModelBroker()

    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection connection: NSXPCConnection) -> Bool {
        guard validateSigningIdentity(pid: connection.processIdentifier) else { return false }
        let brokerInterface = NSXPCInterface(with: FoundationModelBrokerProtocol.self)
        brokerInterface.setInterface(
            NSXPCInterface(with: FoundationModelStreamSink.self),
            for: NSSelectorFromString("stream:to:"),
            argumentIndex: 1,
            ofReply: false
        )
        connection.exportedInterface = brokerInterface
        connection.exportedObject = broker
        connection.resume()
        return true
    }

    private func validateSigningIdentity(pid: pid_t) -> Bool {
        let attributes = [kSecGuestAttributePid as String: pid] as CFDictionary
        var code: SecCode?
        var requirement: SecRequirement?
        guard let teamIdentifier = ownTeamIdentifier() else { return false }
        let requirementText = "identifier \"\(AppleFoundationModelsCallerPolicy.helperIdentifier)\" and anchor apple generic and certificate leaf[subject.OU] = \"\(teamIdentifier)\""
        guard SecCodeCopyGuestWithAttributes(nil, attributes, [], &code) == errSecSuccess,
              let code,
              SecRequirementCreateWithString(requirementText as CFString, [], &requirement) == errSecSuccess,
              let requirement else { return false }
        return SecCodeCheckValidity(code, [], requirement) == errSecSuccess
    }

    private func ownTeamIdentifier() -> String? {
        var ownCode: SecCode?
        var ownStaticCode: SecStaticCode?
        var information: CFDictionary?
        guard SecCodeCopySelf([], &ownCode) == errSecSuccess,
              let ownCode,
              SecCodeCopyStaticCode(ownCode, [], &ownStaticCode) == errSecSuccess,
              let ownStaticCode,
              SecCodeCopySigningInformation(ownStaticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &information) == errSecSuccess,
              let values = information as? [String: Any],
              let teamIdentifier = values[kSecCodeInfoTeamIdentifier as String] as? String,
              !teamIdentifier.isEmpty,
              teamIdentifier.allSatisfy({ $0.isASCII && ($0.isUppercase || $0.isNumber) }) else {
            return nil
        }
        return teamIdentifier
    }
}

private let delegate = BrokerDelegate()
private let listener = NSXPCListener.service()
listener.delegate = delegate
listener.resume()
RunLoop.current.run()
