public enum PromptAnalysisState: Equatable {
    case idle
    case analysing
    case result(RoutingDecision)
    case failed(String)

    public var isIdle: Bool {
        if case .idle = self {
            return true
        }
        return false
    }

    public var isAnalysing: Bool {
        if case .analysing = self {
            return true
        }
        return false
    }
}
