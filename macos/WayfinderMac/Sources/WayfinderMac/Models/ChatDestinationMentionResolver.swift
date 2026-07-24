import Foundation

public enum ChatDestinationMentionResolution: Equatable, Sendable {
    case none
    case resolved(destination: ChatDestination, prompt: String)
    case ambiguous(token: String, candidates: [ChatDestination])
    case unknown(token: String)
}

/// Resolves only a leading `@destination` token.
///
/// Canonical route identifiers always win over friendly aliases. Friendly
/// aliases are accepted only when they identify one destination, so an
/// ambiguous or unknown mention remains ordinary prompt text.
public enum ChatDestinationMentionResolver {
    public static func resolve(
        draft: String,
        destinations: [ChatDestination]
    ) -> ChatDestinationMentionResolution {
        guard let mention = leadingMention(in: draft), !mention.token.isEmpty else {
            return draft.first == "@" ? .unknown(token: "") : .none
        }

        let token = mention.token.lowercased()
        let candidates = mentionDestinations(from: destinations)

        if token == "local" {
            return .resolved(destination: .preferLocal, prompt: mention.prompt)
        }
        if token == "hosted" {
            return .resolved(destination: .preferHosted, prompt: mention.prompt)
        }

        let exactMatches = candidates.filter {
            $0.routeName?.caseInsensitiveCompare(mention.token) == .orderedSame
        }
        if exactMatches.count == 1, let destination = exactMatches.first {
            return .resolved(destination: destination, prompt: mention.prompt)
        }
        if exactMatches.count > 1 {
            return .ambiguous(token: mention.token, candidates: exactMatches)
        }

        if token == "codex" {
            let codexMatches = candidates.filter {
                $0.isChatGPTAccount && $0.isAvailable
            }
            if codexMatches.count == 1, let destination = codexMatches.first {
                return .resolved(destination: destination, prompt: mention.prompt)
            }
            if codexMatches.count > 1 {
                return .ambiguous(token: mention.token, candidates: codexMatches)
            }
            return .unknown(token: mention.token)
        }

        let normalizedToken = normalizedAlias(mention.token)
        let friendlyMatches = candidates.filter {
            normalizedAlias($0.title) == normalizedToken
        }
        if friendlyMatches.count == 1, let destination = friendlyMatches.first {
            return .resolved(destination: destination, prompt: mention.prompt)
        }
        if friendlyMatches.count > 1 {
            return .ambiguous(token: mention.token, candidates: friendlyMatches)
        }

        return .unknown(token: mention.token)
    }

    public static func suggestions(
        for draft: String,
        destinations: [ChatDestination]
    ) -> [ChatDestination] {
        guard let query = activeMentionQuery(in: draft) else {
            return []
        }
        let normalizedQuery = normalizedAlias(query)
        return mentionDestinations(from: destinations).filter { destination in
            guard !normalizedQuery.isEmpty else { return true }
            return searchTerms(for: destination).contains {
                $0.hasPrefix(normalizedQuery)
            }
        }
    }

    public static func removingActiveMention(from draft: String) -> String {
        guard draft.first == "@" else { return draft }
        let tokenEnd = draft.firstIndex(where: \.isWhitespace) ?? draft.endIndex
        return String(draft[tokenEnd...])
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func destination(
        forGatewayModelValue routeName: String,
        destinations: [ChatDestination]
    ) -> ChatDestination? {
        mentionDestinations(from: destinations).first {
            $0.gatewayModelValue.caseInsensitiveCompare(routeName) == .orderedSame
        }
    }

    private static func leadingMention(
        in draft: String
    ) -> (token: String, prompt: String)? {
        guard draft.first == "@" else { return nil }
        let tokenStart = draft.index(after: draft.startIndex)
        let tokenEnd = draft[tokenStart...].firstIndex(where: \.isWhitespace) ?? draft.endIndex
        let token = String(draft[tokenStart..<tokenEnd])
        let prompt = String(draft[tokenEnd...])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (token, prompt)
    }

    private static func activeMentionQuery(in draft: String) -> String? {
        guard draft.first == "@",
              !draft.dropFirst().contains(where: \.isWhitespace) else {
            return nil
        }
        return String(draft.dropFirst())
    }

    private static func mentionDestinations(
        from destinations: [ChatDestination]
    ) -> [ChatDestination] {
        var seen = Set<String>()
        return ([.preferLocal, .preferHosted] + destinations)
            .filter { !$0.isAutomatic }
            .filter { seen.insert($0.gatewayModelValue.lowercased()).inserted }
    }

    private static func searchTerms(for destination: ChatDestination) -> [String] {
        var terms = [
            destination.gatewayModelValue,
            destination.title,
            destination.defaultTitle,
            destination.providerName ?? "",
        ].map(normalizedAlias)
        if destination == .preferLocal {
            terms.append("local")
        }
        if destination == .preferHosted {
            terms.append("hosted")
        }
        if destination.isChatGPTAccount {
            terms.append(contentsOf: ["codex", "chatgpt"])
        }
        return terms
    }

    private static func normalizedAlias(_ value: String) -> String {
        value.lowercased()
            .unicodeScalars
            .reduce(into: "") { result, scalar in
                if CharacterSet.alphanumerics.contains(scalar) {
                    result.unicodeScalars.append(scalar)
                } else if result.last != "-" {
                    result.append("-")
                }
            }
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }
}
