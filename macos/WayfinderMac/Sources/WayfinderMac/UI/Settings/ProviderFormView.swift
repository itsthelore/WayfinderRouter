import SwiftUI

public struct ProviderFormView: View {
    let provider: ProviderKind

    @State private var keyValue = ""
    @State private var status: CredentialStatus = .unknown
    @State private var isWorking = false
    @State private var message: SettingsActionMessage?

    private let keychain = KeychainCredentialStore()
    private let gatewayService = GatewayServiceController()

    public init(provider: ProviderKind) {
        self.provider = provider
    }

    public var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: detail.symbolName)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(effectiveStatus.tint)
                    .frame(width: 20)
                VStack(alignment: .leading, spacing: 2) {
                    Text(detail.displayName)
                        .font(.headline)
                    Text(detail.baseURL)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                StatusLabel(status: effectiveStatus)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)

            Divider()

            VStack(spacing: 0) {
                ExistingKeyRow(title: "Provider", value: detail.providerName, symbolName: "building.2")
                ExistingKeyRow(title: "Models", value: detail.modelSummary, symbolName: "cpu")
                ProviderModelListRow(models: detail.models)
                ExistingKeyRow(title: "Key env var", value: detail.keyEnvironmentVariable ?? "none", symbolName: "terminal")
                ExistingKeyRow(title: "Status", value: effectiveStatus.title, symbolName: effectiveStatus.symbolName, status: effectiveStatus)
            }

            if let envVar = detail.keyEnvironmentVariable, provider != .custom {
                Divider()
                keyEditor(envVar: envVar)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
            } else if detail.isKeyless {
                Divider()
                InlineSettingsNote(
                    symbolName: "desktopcomputer",
                    text: "This provider is keyless; the gateway talks to a local server."
                )
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
            } else {
                Divider()
                InlineSettingsNote(
                    symbolName: "clock",
                    text: "Custom provider editing is coming soon."
                )
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
            }
        }
        .background(WayfinderTheme.panel.opacity(0.62))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
        .task(id: provider) {
            keyValue = ""
            message = nil
            await refreshStatus()
        }
    }

    private var detail: ProviderCredentialDetail {
        provider.credentialDetail
    }

    private var effectiveStatus: CredentialStatus {
        if detail.isKeyless {
            return .local
        }
        if provider == .custom {
            return .comingSoon
        }
        return status
    }

    private func keyEditor(envVar: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                SecureField("\(envVar) key", text: $keyValue)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                    .disabled(isWorking)
                    .onSubmit {
                        if canSave {
                            saveKey(envVar: envVar)
                        }
                    }

                Button {
                    saveKey(envVar: envVar)
                } label: {
                    Text(isWorking ? "Saving" : "Save")
                }
                .disabled(!canSave || isWorking)

                if status == .keyPresent {
                    Button(role: .destructive) {
                        removeKey(envVar: envVar)
                    } label: {
                        Text("Remove")
                    }
                    .disabled(isWorking)
                }
            }

            HStack(spacing: 8) {
                Image(systemName: message?.symbolName ?? "info.circle")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(message?.tint ?? .secondary)
                    .frame(width: 18)
                Text(message?.text ?? "Stored under service \(KeychainCredentialStore.serviceName), account \(envVar).")
                    .font(.caption)
                    .foregroundStyle(message?.tint ?? .secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
            }
        }
    }

    private var canSave: Bool {
        !keyValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func refreshStatus() async {
        guard let envVar = detail.keyEnvironmentVariable, !detail.isKeyless, provider != .custom else {
            status = effectiveStatus
            return
        }
        status = await keychain.contains(envVar: envVar) ? .keyPresent : .keyMissing
    }

    private func saveKey(envVar: String) {
        let key = keyValue
        isWorking = true
        message = nil
        Task {
            do {
                try await keychain.store(envVar: envVar, key: key)
                try? await gatewayService.restart()
                keyValue = ""
                status = .keyPresent
                message = SettingsActionMessage(
                    text: "Saved to Keychain. Restart the gateway from Gateway settings if the app still reports a missing key.",
                    tint: WayfinderTheme.local,
                    symbolName: "checkmark.circle"
                )
            } catch {
                message = SettingsActionMessage(
                    text: error.localizedDescription,
                    tint: .red,
                    symbolName: "exclamationmark.triangle"
                )
            }
            isWorking = false
            await refreshStatus()
        }
    }

    private func removeKey(envVar: String) {
        isWorking = true
        message = nil
        Task {
            do {
                try await keychain.delete(envVar: envVar)
                try? await gatewayService.restart()
                keyValue = ""
                status = .keyMissing
                message = SettingsActionMessage(
                    text: "Removed from Keychain. Restart the gateway from Gateway settings if status does not update.",
                    tint: WayfinderTheme.local,
                    symbolName: "checkmark.circle"
                )
            } catch {
                message = SettingsActionMessage(
                    text: error.localizedDescription,
                    tint: .red,
                    symbolName: "exclamationmark.triangle"
                )
            }
            isWorking = false
            await refreshStatus()
        }
    }
}

private struct StatusLabel: View {
    let status: CredentialStatus

    var body: some View {
        Label(status.title, systemImage: status.symbolName)
            .font(.caption.weight(.medium))
            .foregroundStyle(status.tint)
            .labelStyle(.titleAndIcon)
            .fixedSize()
    }
}

private struct SettingsActionMessage {
    let text: String
    let tint: Color
    let symbolName: String
}

private struct InlineSettingsNote: View {
    let symbolName: String
    let text: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbolName)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 18)
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }
}

private struct ProviderModelListRow: View {
    let models: [String]

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "list.bullet")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 20)
            Text("Uses")
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(width: 92, alignment: .leading)
            VStack(alignment: .leading, spacing: 5) {
                ForEach(models, id: \.self) { model in
                    Text(model)
                        .font(.callout)
                        .foregroundStyle(.primary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            Spacer(minLength: 12)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
    }
}
