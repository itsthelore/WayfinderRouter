import Foundation
import Security

private let serviceName = "wayfinder-router"
private let maximumAccountBytes = 256
private let helperRequirement = "identifier \"com.wayfinder.router.helper\" and anchor apple generic"

@objc private protocol CredentialBrokerProtocol {
    func resolve(account: String, withReply reply: @escaping (Data?, String?) -> Void)
}

private final class CredentialBroker: NSObject, CredentialBrokerProtocol {
    func resolve(account: String, withReply reply: @escaping (Data?, String?) -> Void) {
        guard !account.isEmpty,
              account.lengthOfBytes(using: .utf8) <= maximumAccountBytes,
              account.range(of: "^[A-Z][A-Z0-9_]*$", options: .regularExpression) != nil
        else {
            reply(nil, "invalid-reference")
            return
        }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: serviceName,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data, data.count <= 16_384 else {
            reply(nil, status == errSecItemNotFound ? "missing" : "unavailable")
            return
        }
        reply(data, nil)
    }
}

private final class BrokerDelegate: NSObject, NSXPCListenerDelegate {
    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection connection: NSXPCConnection) -> Bool {
        guard validateSigningIdentity(pid: connection.processIdentifier) else { return false }
        connection.exportedInterface = NSXPCInterface(with: CredentialBrokerProtocol.self)
        connection.exportedObject = CredentialBroker()
        connection.resume()
        return true
    }

    private func validateSigningIdentity(pid: pid_t) -> Bool {
        let attributes = [kSecGuestAttributePid as String: pid] as CFDictionary
        var code: SecCode?
        var requirement: SecRequirement?
        guard SecCodeCopyGuestWithAttributes(nil, attributes, [], &code) == errSecSuccess,
              let code,
              SecRequirementCreateWithString(helperRequirement as CFString, [], &requirement) == errSecSuccess,
              let requirement
        else { return false }
        return SecCodeCheckValidity(code, [], requirement) == errSecSuccess
    }
}

private let delegate = BrokerDelegate()
private let listener = NSXPCListener.service()
listener.delegate = delegate
listener.resume()
RunLoop.current.run()
