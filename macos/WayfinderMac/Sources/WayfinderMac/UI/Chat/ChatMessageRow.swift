import SwiftUI

public struct ChatMessageRow: View {
    let message: ChatMessage

    public init(message: ChatMessage) {
        self.message = message
    }

    public var body: some View {
        switch message.role {
        case .user:
            userRow
        case .router:
            routerRow
        }
    }

    private var userRow: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "person.crop.circle")
                .font(.system(size: 18))
                .foregroundStyle(.secondary)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline) {
                    Text(message.text)
                        .font(.body)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 18)
                    Text(message.createdAt.formatted(date: .omitted, time: .shortened))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var routerRow: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "point.topleft.down.curvedto.point.bottomright.up")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(message.decision?.route.accentColor ?? WayfinderTheme.local)
                .frame(width: 24)
            if let decision = message.decision {
                RoutingResponseCard(decision: decision)
            } else {
                Text(message.text)
                    .foregroundStyle(.secondary)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(WayfinderTheme.panel, in: RoundedRectangle(cornerRadius: 14))
            }
        }
    }
}
