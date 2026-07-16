import AppKit
import SwiftUI

public struct SetupAssistantView: View {
    @ObservedObject private var state: SetupState
    private let onDismiss: () -> Void
    @State private var credentialValues: [String: String] = [:]

    public init(state: SetupState, onDismiss: @escaping () -> Void) {
        self.state = state; self.onDismiss = onDismiss
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            content.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            Divider()
            actionRow.padding(.horizontal, 24).frame(height: 62)
        }
        .frame(minWidth: 520, minHeight: 420)
        .task { if state.step == .checking { await state.assess() } }
    }

    @ViewBuilder private var content: some View {
        switch state.step {
        case .checking:
            stepHeader("Checking Wayfinder", "Looking for the router, configuration, and gateway service.")
            ProgressView().controlSize(.small).padding(.top, 24)
        case .toolsMissing:
            stepHeader("Tools Missing", "The Homebrew gateway command is required before setup can continue.")
            commandBox("brew install wayfinder-router")
        case .welcome:
            stepHeader("Set up Wayfinder", "Wayfinder routes each request to a configured local or hosted model.")
            Text("Routing decisions are computed locally without a model call.").foregroundStyle(.secondary).padding(.top, 18)
        case .existingConfiguration:
            stepHeader("Existing Configuration", "Wayfinder found a configuration and will not overwrite it.")
            Text(GatewayServiceController.defaultConfigPath()).font(.system(.callout, design: .monospaced)).textSelection(.enabled).padding(.top, 18)
        case .chooseRouting:
            stepHeader("Choose routing", "Choose how requests should be routed. Preset rules are created by the gateway.")
            VStack(alignment: .leading, spacing: 12) {
                ForEach(state.approvedPresets) { preset in presetRow(preset) }
            }.padding(.top, 18)
            if let guidance = state.appleAvailability.setupGuidance {
                Text(guidance).font(.caption).foregroundStyle(.secondary).padding(.top, 14)
            }
        case .requirements:
            stepHeader("Check requirements", "The selected preset needs a local runtime that Wayfinder will not install or launch.")
            commandBox("brew install \(state.missingRuntime ?? "ollama")")
        case .credentials:
            stepHeader("Add credentials", "Keys are written directly to macOS Keychain and are not added to the configuration.")
            VStack(alignment: .leading, spacing: 16) {
                ForEach(state.requiredCredentials, id: \.environmentVariable) { credential in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(credential.provider) API key").font(.headline)
                        SecureField("API key", text: binding(for: credential.environmentVariable))
                            .textFieldStyle(.roundedBorder)
                            .accessibilityLabel("\(credential.provider) API key")
                    }
                }
            }.padding(.top, 18)
        case .configure:
            stepHeader("Configure and start", "Wayfinder will create the configuration, update the launch agent, save keys, restart, and check the gateway.")
            if let stage = state.progressStage {
                VStack(alignment: .leading, spacing: 10) {
                    ProgressView(value: Double(stage.rawValue + 1), total: Double(SetupProgressStage.allCases.count))
                    Text("Step \(stage.rawValue + 1) of \(SetupProgressStage.allCases.count): \(stage.title)").font(.callout)
                }.padding(.top, 24).accessibilityElement(children: .combine)
            }
            if let failure = state.failureMessage { errorText(failure) }
        case .result:
            resultContent
        }
    }

    private func stepHeader(_ title: String, _ description: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            Text(description).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func presetRow(_ preset: SetupPreset) -> some View {
        Button { state.selectedPresetID = preset.id } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: state.selectedPresetID == preset.id ? "largecircle.fill.circle" : "circle")
                VStack(alignment: .leading, spacing: 3) {
                    Text(preset.title).font(.headline)
                    Text(preset.summary).foregroundStyle(.secondary)
                    Text(preset.requirement).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }.contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityAddTraits(state.selectedPresetID == preset.id ? .isSelected : [])
    }

    private func commandBox(_ command: String) -> some View {
        HStack {
            Text(command).font(.system(.callout, design: .monospaced)).textSelection(.enabled)
            Spacer()
            Button("Copy") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(command, forType: .string) }
        }.padding(12).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 7)).padding(.top, 20)
    }

    private var resultContent: some View {
        VStack(alignment: .leading, spacing: 14) {
            stepHeader(state.result?.isDegraded == true ? "Wayfinder needs attention" : "Wayfinder is ready", state.result?.isDegraded == true ? "Configuration finished, but a requirement is still missing." : "The gateway is configured and ready for routing.")
            if let result = state.result {
                LabeledContent("Routing preset", value: result.presetID)
                LabeledContent("Gateway", value: result.gatewayAddress)
                LabeledContent("Configured endpoints", value: "\(result.endpointCount)")
                LabeledContent("Endpoint readiness", value: result.missingKeys.isEmpty ? "Configured and key-ready" : "Missing: \(result.missingKeys.joined(separator: ", "))")
            }
        }
    }

    private func errorText(_ text: String) -> some View { Text(text).font(.callout).foregroundStyle(.red).padding(.top, 16).textSelection(.enabled) }

    @ViewBuilder private var actionRow: some View {
        HStack {
            if [.chooseRouting, .requirements, .credentials, .configure].contains(state.step) && !state.isMutating { Button("Back") { state.back() } }
            Spacer()
            if [.welcome, .chooseRouting, .requirements, .credentials].contains(state.step) { Button("Set Up Later") { deferSetup() }.keyboardShortcut(.cancelAction) }
            primaryAction
        }
    }

    @ViewBuilder private var primaryAction: some View {
        switch state.step {
        case .checking: EmptyView()
        case .toolsMissing: Button("Check Again") { Task { await state.assess() } }.keyboardShortcut(.defaultAction)
        case .welcome: Button("Continue") { state.continueFromWelcome() }.keyboardShortcut(.defaultAction)
        case .existingConfiguration: Button("Use Existing Configuration") { deferSetup() }.keyboardShortcut(.defaultAction)
        case .chooseRouting: Button("Continue") { state.chooseRouting() }.keyboardShortcut(.defaultAction)
        case .requirements: Button("Check Again") { state.requirementsChecked() }.keyboardShortcut(.defaultAction)
        case .credentials: Button("Continue") { state.credentialsReady() }.disabled(!credentialsComplete).keyboardShortcut(.defaultAction)
        case .configure:
            if state.isMutating { Button("Cancel") { state.cancel() } }
            else { Button("Configure and Start") { beginSetup() }.keyboardShortcut(.defaultAction) }
        case .result: Button("Open Wayfinder") { onDismiss() }.keyboardShortcut(.defaultAction)
        }
    }

    private var credentialsComplete: Bool { state.requiredCredentials.allSatisfy { !(credentialValues[$0.environmentVariable] ?? "").isEmpty } }
    private func binding(for name: String) -> Binding<String> { Binding(get: { credentialValues[name, default: ""] }, set: { credentialValues[name] = $0 }) }
    private func beginSetup() {
        let values = credentialValues
        state.configure(credentials: values) {
            credentialValues.removeAll(keepingCapacity: false)
            NotificationCenter.default.post(name: .wayfinderSetupDidChange, object: nil)
        }
    }
    private func deferSetup() {
        credentialValues.removeAll(keepingCapacity: false)
        UserDefaults.standard.set(true, forKey: "Wayfinder.Setup.Deferred")
        NotificationCenter.default.post(name: .wayfinderSetupDidChange, object: nil)
        onDismiss()
    }
}
