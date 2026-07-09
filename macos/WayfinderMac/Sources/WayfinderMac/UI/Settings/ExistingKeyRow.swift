import SwiftUI

public struct ExistingKeyRow: View {
    let title: String
    let value: String
    let symbolName: String
    let status: CredentialStatus?

    public init(title: String, value: String, symbolName: String, status: CredentialStatus? = nil) {
        self.title = title
        self.value = value
        self.symbolName = symbolName
        self.status = status
    }

    public var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbolName)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(status?.tint ?? .secondary)
                .frame(width: 20)
            Text(title)
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(width: 92, alignment: .leading)
            Text(value)
                .font(.callout)
                .foregroundStyle(status?.tint ?? .primary)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer(minLength: 12)
        }
        .frame(height: 38)
        .padding(.horizontal, 12)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WayfinderTheme.hairline)
                .frame(height: 1)
        }
    }
}
