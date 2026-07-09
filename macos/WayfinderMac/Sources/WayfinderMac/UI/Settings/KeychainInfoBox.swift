import SwiftUI

public struct KeychainInfoBox: View {
    public init() {}

    public var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "lock")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 18)
            Text("Keys are stored in the macOS Keychain and read by scaffolded configs through api_key_cmd.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 2)
    }
}
