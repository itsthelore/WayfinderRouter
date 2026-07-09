import Foundation

public enum RoutingSettingsMode: String, Codable, Sendable {
    case binary
    case tiered
    case classifier
}

public struct RoutingTierRow: Identifiable, Codable, Equatable, Sendable {
    public var id: String { "\(model)-\(minScore)" }
    public var model: String
    public var minScore: Double
    public var editable: Bool

    public init(model: String, minScore: Double, editable: Bool = true) {
        self.model = model
        self.minScore = minScore
        self.editable = editable
    }
}

public struct RoutingWeightRow: Identifiable, Codable, Equatable, Sendable {
    public var id: String
    public var displayLabel: String
    public var value: Double
    public var defaultValue: Double

    public init(id: String, displayLabel: String, value: Double, defaultValue: Double) {
        self.id = id
        self.displayLabel = displayLabel
        self.value = value
        self.defaultValue = defaultValue
    }
}

public struct RoutingSettingsState: Codable, Equatable, Sendable {
    public static let defaultThreshold = 0.5

    public var mode: RoutingSettingsMode
    public var threshold: Double
    public var tiers: [RoutingTierRow]
    public var weights: [RoutingWeightRow]
    public var classifierModels: [String]
    public var dirty: Bool
    public var saving: Bool
    public var error: String?

    public init(
        mode: RoutingSettingsMode = .binary,
        threshold: Double = Self.defaultThreshold,
        tiers: [RoutingTierRow] = [
            RoutingTierRow(model: "local", minScore: 0.0, editable: false),
            RoutingTierRow(model: "cloud", minScore: Self.defaultThreshold),
        ],
        weights: [RoutingWeightRow] = Self.defaultWeights,
        classifierModels: [String] = [],
        dirty: Bool = false,
        saving: Bool = false,
        error: String? = nil
    ) {
        self.mode = mode
        self.threshold = threshold
        self.tiers = tiers
        self.weights = weights
        self.classifierModels = classifierModels
        self.dirty = dirty
        self.saving = saving
        self.error = error
    }

    public static let defaultWeights: [RoutingWeightRow] = [
        RoutingWeightRow(id: "word_count", displayLabel: "Word Count", value: 3.0, defaultValue: 3.0),
        RoutingWeightRow(id: "heading_count", displayLabel: "Heading Count", value: 1.5, defaultValue: 1.5),
        RoutingWeightRow(id: "max_heading_depth", displayLabel: "Max Heading Depth", value: 1.0, defaultValue: 1.0),
        RoutingWeightRow(id: "list_item_count", displayLabel: "List Item Count", value: 2.0, defaultValue: 2.0),
        RoutingWeightRow(id: "link_count", displayLabel: "Link Count", value: 1.0, defaultValue: 1.0),
        RoutingWeightRow(id: "code_block_count", displayLabel: "Code Block Count", value: 1.5, defaultValue: 1.5),
        RoutingWeightRow(id: "table_row_count", displayLabel: "Table Row Count", value: 1.0, defaultValue: 1.0),
        RoutingWeightRow(id: "reasoning_term_count", displayLabel: "Reasoning Term Count", value: 0.0, defaultValue: 0.0),
        RoutingWeightRow(id: "math_symbol_count", displayLabel: "Math Symbol Count", value: 0.0, defaultValue: 0.0),
        RoutingWeightRow(id: "constraint_term_count", displayLabel: "Constraint Term Count", value: 0.0, defaultValue: 0.0),
        RoutingWeightRow(id: "question_count", displayLabel: "Question Count", value: 0.0, defaultValue: 0.0),
    ]

    public static let weightHelpText: [String: String] = [
        "word_count": "Raises the score as prompts get longer.",
        "heading_count": "Raises the score when prompts use headings for structure.",
        "max_heading_depth": "Raises the score for deeper heading structure.",
        "list_item_count": "Raises the score for list-heavy or multi-step prompts.",
        "link_count": "Raises the score when prompts include links.",
        "code_block_count": "Raises the score when prompts include code blocks.",
        "table_row_count": "Raises the score when prompts include tables.",
        "reasoning_term_count": "Opt-in lexical signal for reasoning terms; shipped off at 0.0.",
        "math_symbol_count": "Opt-in lexical signal for math notation; shipped off at 0.0.",
        "constraint_term_count": "Opt-in lexical signal for constraint words; shipped off at 0.0.",
        "question_count": "Opt-in lexical signal for question marks; shipped off at 0.0.",
    ]

    public mutating func resetWeightsToDefaults() {
        for index in weights.indices {
            weights[index].value = weights[index].defaultValue
        }
    }

    public func model(for score: Double) -> String {
        tiers
            .sorted { $0.minScore < $1.minScore }
            .last { score >= $0.minScore }?
            .model ?? tiers.first?.model ?? "local"
    }

    public func routingTOML() -> String {
        var lines: [String] = []
        lines.append("[routing]")
        if mode == .binary {
            lines.append("threshold = \(Self.format(threshold))")
        }
        let editedWeights = weights.filter { abs($0.value - $0.defaultValue) > 0.000_001 }
        if !editedWeights.isEmpty {
            let pairs = editedWeights.map { "\($0.id) = \(Self.format($0.value))" }
            lines.append("weights = { \(pairs.joined(separator: ", ")) }")
        }
        if mode == .tiered {
            for tier in tiers {
                lines.append("")
                lines.append("[[routing.tiers]]")
                lines.append("min_score = \(Self.format(tier.minScore))")
                lines.append("model = \(Self.tomlString(tier.model))")
            }
        }
        return lines.joined(separator: "\n") + "\n"
    }

    public mutating func normalize() {
        threshold = threshold.clamped(to: 0...1)
        for index in tiers.indices {
            tiers[index].minScore = tiers[index].minScore.clamped(to: 0...1)
            if index == 0 {
                tiers[index].minScore = 0.0
                tiers[index].editable = false
            }
        }
        for index in weights.indices {
            weights[index].value = max(0, weights[index].value)
        }
    }

    private static func format(_ value: Double) -> String {
        String(format: "%.3f", value)
            .replacingOccurrences(of: #"0+$"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\.$"#, with: ".0", options: .regularExpression)
    }

    private static func tomlString(_ value: String) -> String {
        let data = try? JSONEncoder().encode(value)
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? "\"\(value)\""
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
