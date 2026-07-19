import Combine
import Foundation

@MainActor
public final class ChatDestinationNameStore: ObservableObject {
    nonisolated public static let maximumNameLength = 48

    @Published public private(set) var overrides: [String: String]

    private let defaults: UserDefaults
    private let storageKey: String

    public init(
        defaults: UserDefaults = .standard,
        storageKey: String = "Wayfinder.ChatDestinationNames"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
        let stored = defaults.dictionary(forKey: storageKey) as? [String: String] ?? [:]
        self.overrides = stored.reduce(into: [:]) { result, entry in
            let normalized = Self.normalized(entry.value)
            if !normalized.isEmpty {
                result[entry.key] = normalized
            }
        }
    }

    public func name(for routeName: String, default defaultName: String) -> String {
        overrides[routeName] ?? defaultName
    }

    public func override(for routeName: String) -> String? {
        overrides[routeName]
    }

    public func setName(_ name: String, for routeName: String) {
        let normalized = Self.normalized(name)
        if normalized.isEmpty {
            overrides.removeValue(forKey: routeName)
        } else {
            overrides[routeName] = normalized
        }
        persist()
    }

    public func resetName(for routeName: String) {
        overrides.removeValue(forKey: routeName)
        persist()
    }

    nonisolated static func normalized(_ name: String) -> String {
        let collapsed = name
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        return String(collapsed.prefix(maximumNameLength))
    }

    private func persist() {
        defaults.set(overrides, forKey: storageKey)
    }
}
