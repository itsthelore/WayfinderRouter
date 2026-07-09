public struct RoutingScore: Equatable, Sendable {
    public let value: Double
    public let threshold: Double

    public init(value: Double, threshold: Double = 0.5) {
        self.value = value
        self.threshold = threshold
    }

    public var clearsThreshold: Bool {
        value >= threshold
    }
}
