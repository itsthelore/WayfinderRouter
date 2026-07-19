import AppKit
import SwiftUI

public struct RoutingSettingsView: View {
    @ObservedObject private var appState: AppState
    @State private var state = RoutingSettingsState()
    @State private var isLoading = true
    @State private var previewPrompt = ""
    @State private var weightsExpanded = false
    @State private var statusMessage: RoutingStatusMessage?

    private let previewScorer = LocalPromptScorer()
    private let store: RoutingConfigStore
    private let configURL = RoutingConfigStore.defaultConfigURL

    public init(
        appState: AppState,
        store: RoutingConfigStore = RoutingConfigStore()
    ) {
        self.appState = appState
        self.store = store
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header

            Form {
                Section("Routing policy") {
                    if isLoading {
                        loadingRow
                    } else {
                            modeContent
                                .disabled(state.saving)
                    }
                }

                if !isLoading {
                    Section("Model names") {
                        modelNamesSection
                    }

                    Section("Preview") {
                            promptPreviewSection
                    }

                    Section("Advanced") {
                            weightsSection
                                .disabled(state.saving)
                    }

                    Section {
                            actionRow
                            if let statusMessage {
                                Divider()
                                inlineMessage(statusMessage)
                            }
                    }
                }

                Section {
                    Text("If routing behavior does not update immediately, restart the gateway from Gateway settings.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
        }
        .padding(.horizontal, 28)
        .padding(.top, 28)
        .padding(.bottom, 18)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task {
            await load()
        }
    }

    private var modelNamesSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Choose personal names for Chat destinations on this Mac. Gateway route IDs stay unchanged.")
                .font(.caption)
                .foregroundStyle(.secondary)

            if appState.chatDestinations.dropFirst().isEmpty {
                Text("No Chat destinations are currently available.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(appState.chatDestinations.dropFirst())) { destination in
                    ChatDestinationNameRow(
                        destination: destination,
                        storedName: appState.chatDestinationNameStore.override(for: destination.id),
                        onSave: { appState.setChatDestinationDisplayName($0, for: destination.id) },
                        onReset: { appState.resetChatDestinationDisplayName(for: destination.id) }
                    )
                }
            }
        }
        .padding(12)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Routing")
                .font(.title3.weight(.semibold))
            Text("Standing gateway thresholds, tiers, and score weights.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }

    private var loadingRow: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)
            Text("Loading routing config")
                .font(.callout)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(12)
    }

    @ViewBuilder
    private var modeContent: some View {
        switch state.mode {
        case .binary:
            binaryThresholdSection
        case .tiered:
            tieredSection
        case .classifier:
            classifierSection
        }
    }

    private var binaryThresholdSection: some View {
        RoutingSliderRow(
            title: "Binary Threshold",
            symbolName: "arrow.left.arrow.right",
            infoTitle: "Binary Threshold",
            infoMessage: "Score at or above this value routes to cloud. Lower scores stay local.",
            value: thresholdBinding,
            range: 0...1,
            valueText: state.threshold.scoreText,
            defaultText: "Default \(RoutingSettingsState.defaultThreshold.scoreText)",
            leadingEndpoint: "Local",
            trailingEndpoint: "Cloud",
            isDisabled: state.mode == .classifier
        )
        .padding(12)
    }

    private var tieredSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                sectionLabel("Tiered Routing", symbolName: "list.bullet.below.rectangle")
                RoutingInfoButton(
                    title: "Tiered Routing",
                    message: "Each tier routes scores at or above its minimum score to that model."
                )
                Spacer()
            }
            ForEach(state.tiers.indices, id: \.self) { index in
                tierRow(index: index)
            }
        }
        .padding(12)
    }

    private var classifierSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionLabel("Classifier Routing", symbolName: "function")
            HStack(spacing: 10) {
                Image(systemName: "lock")
                    .foregroundStyle(.secondary)
                    .frame(width: 20)
                Text("Classifier configs are shown read-only in this version.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
                Text(state.classifierModels.joined(separator: ", "))
                    .font(.callout.monospaced())
                    .lineLimit(1)
                    .truncationMode(.middle)
                Button("Open Config") {
                    NSWorkspace.shared.activateFileViewerSelecting([configURL])
                }
            }
        }
        .padding(12)
    }

    private func tierRow(index: Int) -> some View {
        let tier = state.tiers[index]
        return HStack(spacing: 10) {
            Text(index == 0 ? "Base" : "Tier \(index + 1)")
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
                .frame(width: 54, alignment: .leading)
            TextField("Model", text: tierModelBinding(index))
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))
            Text("min")
                .font(.caption)
                .foregroundStyle(.secondary)
            TextField("Min score", value: tierScoreBinding(index), format: .number.precision(.fractionLength(2)))
                .textFieldStyle(.roundedBorder)
                .frame(width: 72)
                .multilineTextAlignment(.trailing)
                .disabled(!tier.editable)
            Slider(value: tierScoreBinding(index), in: 0...1)
                .controlSize(.small)
                .tint(WayfinderTheme.local.opacity(0.82))
                .disabled(!tier.editable)
                .frame(width: 132)
        }
    }

    private var promptPreviewSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                sectionLabel("Prompt Preview", symbolName: "text.bubble")
                RoutingInfoButton(
                    title: "Prompt Preview",
                    message: "Enter a sample prompt to see how the current unsaved routing settings would route it."
                )
                Spacer()
                if state.mode == .classifier {
                    Text("Open config to inspect classifier routing")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            TextEditor(text: $previewPrompt)
                .font(.callout)
                .scrollContentBackground(.hidden)
                .frame(minHeight: 74)
                .padding(8)
                .background(Color(nsColor: .textBackgroundColor).opacity(0.72), in: RoundedRectangle(cornerRadius: 7))
                .overlay(
                    RoundedRectangle(cornerRadius: 7)
                        .stroke(WayfinderTheme.hairline, lineWidth: 1)
                )
                .disabled(state.mode == .classifier)
                .accessibilityLabel("Prompt preview input")

            promptPreviewResult
        }
        .padding(12)
    }

    @ViewBuilder
    private var promptPreviewResult: some View {
        if state.mode == .classifier {
            EmptyView()
        } else if previewPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            Text("Paste a prompt to preview its route with the current threshold and weights.")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else if let decision = previewDecision {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Label(decision.route.displayName, systemImage: decision.route.symbolName)
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(decision.route.accentColor)
                        .frame(width: 92, alignment: .leading)
                    Text(decision.provider)
                        .font(.callout.monospaced())
                        .foregroundStyle(.primary)
                        .lineLimit(1)
                    Spacer()
                    Text(decision.score.scoreText)
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(decision.route.accentColor)
                    Text("score")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ProgressView(value: decision.score, total: 1)
                    .controlSize(.small)
                    .tint(decision.route.accentColor.opacity(0.82))

                Text(decision.explanation)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(10)
            .background(decision.route.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
        }
    }

    private var weightsSection: some View {
        DisclosureGroup(isExpanded: $weightsExpanded) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Text("Shipped defaults: local >= 0.00, cloud >= 0.50; lexical weights ship off.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Reset to Defaults") {
                        state.resetWeightsToDefaults()
                        markDirty()
                    }
                    .controlSize(.small)
                    .disabled(state.mode == .classifier || state.saving)
                }

                ForEach(state.weights.indices, id: \.self) { index in
                    weightRow(index: index)
                }
            }
            .padding(.top, 8)
        } label: {
            weightsDisclosureLabel
        }
        .padding(12)
    }

    private var weightsDisclosureLabel: some View {
        HStack(spacing: 8) {
            sectionLabel("Feature Weights", symbolName: "slider.horizontal.3")
            RoutingInfoButton(
                title: "Feature Weights",
                message: "Weights decide how much each feature adds to the routing score. Higher scores are more likely to route to cloud."
            )
            Spacer()
            Text(weightsSummaryText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private func weightRow(index: Int) -> some View {
        RoutingSliderRow(
            title: state.weights[index].displayLabel,
            infoTitle: state.weights[index].displayLabel,
            infoMessage: RoutingSettingsState.weightHelpText[state.weights[index].id] ?? "Adjusts this feature's contribution to the routing score.",
            value: weightBinding(index),
            range: 0...8,
            valueText: state.weights[index].value.formatted(.number.precision(.fractionLength(2))),
            defaultText: "Default \(state.weights[index].defaultValue.formatted(.number.precision(.fractionLength(2))))",
            isDisabled: state.mode == .classifier
        )
    }

    private var actionRow: some View {
        HStack(spacing: 8) {
            Button {
                Task { await save() }
            } label: {
                Text(state.saving ? "Saving" : "Save")
            }
            .disabled(!state.dirty || state.saving || state.mode == .classifier)

            Button("Reload") {
                Task { await load() }
            }
            .disabled(state.saving)

            Button("Open Config") {
                NSWorkspace.shared.activateFileViewerSelecting([configURL])
            }

            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    private func sectionLabel(_ title: String, symbolName: String) -> some View {
        Label(title, systemImage: symbolName)
            .font(.callout.weight(.semibold))
            .foregroundStyle(.primary)
    }

    private func inlineMessage(_ message: RoutingStatusMessage) -> some View {
        HStack(spacing: 8) {
            Image(systemName: message.symbolName)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(message.tint)
                .frame(width: 18)
            Text(message.text)
                .font(.caption)
                .foregroundStyle(message.tint)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    private var thresholdBinding: Binding<Double> {
        Binding {
            state.threshold
        } set: { newValue in
            state.threshold = newValue.clamped(to: 0...1)
            if state.tiers.count >= 2 {
                state.tiers[1].minScore = state.threshold
            }
            markDirty()
        }
    }

    private func tierModelBinding(_ index: Int) -> Binding<String> {
        Binding {
            state.tiers[index].model
        } set: { newValue in
            state.tiers[index].model = newValue
            markDirty()
        }
    }

    private func tierScoreBinding(_ index: Int) -> Binding<Double> {
        Binding {
            state.tiers[index].minScore
        } set: { newValue in
            state.tiers[index].minScore = index == 0 ? 0 : newValue.clamped(to: 0...1)
            markDirty()
        }
    }

    private func weightBinding(_ index: Int) -> Binding<Double> {
        Binding {
            state.weights[index].value
        } set: { newValue in
            state.weights[index].value = max(0, newValue)
            markDirty()
        }
    }

    private var weightsSummaryText: String {
        let changedCount = state.weights.filter { abs($0.value - $0.defaultValue) > 0.000_001 }.count
        let changedText = changedCount == 1 ? "1 changed" : "\(changedCount) changed"
        return "\(changedText), defaults visible when expanded"
    }

    private var previewDecision: RoutingDecision? {
        try? previewScorer.analyse(
            prompt: previewPrompt,
            threshold: state.threshold,
            tiers: state.tiers,
            weights: state.weights
        )
    }

    private func markDirty() {
        statusMessage = nil
        state.dirty = true
    }

    private func load() async {
        isLoading = true
        statusMessage = nil
        do {
            state = try await store.load()
            statusMessage = nil
        } catch {
            state.error = error.localizedDescription
            statusMessage = RoutingStatusMessage(
                text: error.localizedDescription,
                tint: .red,
                symbolName: "exclamationmark.triangle"
            )
        }
        isLoading = false
    }

    private func save() async {
        state.saving = true
        statusMessage = nil
        do {
            try await store.save(state)
            state.dirty = false
            statusMessage = RoutingStatusMessage(
                text: "Saved routing config. Restart the gateway from Gateway settings if routing does not update.",
                tint: WayfinderTheme.local,
                symbolName: "checkmark.circle"
            )
        } catch {
            statusMessage = RoutingStatusMessage(
                text: error.localizedDescription,
                tint: .red,
                symbolName: "exclamationmark.triangle"
            )
        }
        state.saving = false
    }
}

private struct RoutingStatusMessage {
    let text: String
    let tint: Color
    let symbolName: String
}

private struct ChatDestinationNameRow: View {
    let destination: ChatDestination
    let storedName: String?
    let onSave: (String) -> Void
    let onReset: () -> Void

    @State private var draft: String

    init(
        destination: ChatDestination,
        storedName: String?,
        onSave: @escaping (String) -> Void,
        onReset: @escaping () -> Void
    ) {
        self.destination = destination
        self.storedName = storedName
        self.onSave = onSave
        self.onReset = onReset
        _draft = State(initialValue: storedName ?? destination.title)
    }

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                TextField("Display name", text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(save)
                    .accessibilityLabel("Display name for \(destination.id)")
                Text("Route ID: \(destination.id) · \(destination.detail)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            Button("Save", action: save)
                .disabled(normalizedDraft.isEmpty || normalizedDraft == destination.title)

            Button("Reset") {
                onReset()
                draft = defaultName
            }
            .disabled(storedName == nil)
        }
        .onChange(of: storedName) { _, newValue in
            draft = newValue ?? defaultName
        }
    }

    private var normalizedDraft: String {
        ChatDestinationNameStore.normalized(draft)
    }

    private var defaultName: String {
        destination.defaultTitle
    }

    private func save() {
        guard !normalizedDraft.isEmpty else { return }
        onSave(normalizedDraft)
        draft = normalizedDraft
    }
}

private struct RoutingSliderRow: View {
    let title: String
    let symbolName: String?
    let infoTitle: String?
    let infoMessage: String?
    @Binding var value: Double
    let range: ClosedRange<Double>
    let valueText: String
    let defaultText: String?
    let resultText: String?
    let leadingEndpoint: String?
    let trailingEndpoint: String?
    let isDisabled: Bool

    init(
        title: String,
        symbolName: String? = nil,
        infoTitle: String? = nil,
        infoMessage: String? = nil,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        valueText: String,
        defaultText: String? = nil,
        resultText: String? = nil,
        leadingEndpoint: String? = nil,
        trailingEndpoint: String? = nil,
        isDisabled: Bool = false
    ) {
        self.title = title
        self.symbolName = symbolName
        self.infoTitle = infoTitle
        self.infoMessage = infoMessage
        self._value = value
        self.range = range
        self.valueText = valueText
        self.defaultText = defaultText
        self.resultText = resultText
        self.leadingEndpoint = leadingEndpoint
        self.trailingEndpoint = trailingEndpoint
        self.isDisabled = isDisabled
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 10) {
                label
                    .frame(width: 178, alignment: .leading)

                if let leadingEndpoint {
                    Text(leadingEndpoint)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(WayfinderTheme.local)
                        .frame(width: 36, alignment: .leading)
                }

                Slider(value: $value, in: range)
                    .controlSize(.small)
                    .tint(WayfinderTheme.local.opacity(0.82))
                    .disabled(isDisabled)
                    .frame(minWidth: 128, idealWidth: 180, maxWidth: 220)
                    .accessibilityLabel(title)
                    .accessibilityValue(valueText)

                if let trailingEndpoint {
                    Text(trailingEndpoint)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(WayfinderTheme.cloud)
                        .frame(width: 42, alignment: .trailing)
                }

                Text(valueText)
                    .font(.callout.monospacedDigit())
                    .foregroundStyle(isDisabled ? .secondary : .primary)
                    .frame(width: 52, alignment: .trailing)

                if let resultText {
                    Text(resultText)
                        .font(.callout.weight(.medium))
                        .foregroundStyle(isDisabled ? .secondary : WayfinderTheme.local)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .frame(width: 112, alignment: .leading)
                }
            }

            if let defaultText {
                Text(defaultText)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .padding(.leading, 188)
            }
        }
        .frame(minHeight: 34)
    }

    private var label: some View {
        HStack(spacing: 6) {
            if let symbolName {
                Image(systemName: symbolName)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 18)
            }
            Text(title)
                .font(.callout.weight(.medium))
                .lineLimit(1)
            if let infoTitle, let infoMessage {
                RoutingInfoButton(title: infoTitle, message: infoMessage)
            }
        }
    }
}

private struct RoutingInfoButton: View {
    let title: String
    let message: String
    @State private var isPresented = false

    var body: some View {
        Button {
            isPresented.toggle()
        } label: {
            Image(systemName: "info.circle")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 18, height: 18)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title) info")
        .accessibilityHint(message)
        .help(message)
        .popover(isPresented: $isPresented, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.callout.weight(.semibold))
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(width: 260, alignment: .leading)
            .padding(12)
        }
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
