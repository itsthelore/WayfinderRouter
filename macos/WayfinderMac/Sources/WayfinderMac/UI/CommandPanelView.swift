import AppKit
import SwiftUI

public struct CommandPanelView: View {
    @EnvironmentObject private var appState: AppState

    public init() {}

    public var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            VStack(alignment: .leading, spacing: 14) {
                PromptInputView(prompt: $appState.prompt, isAnalysing: appState.analysis.isAnalysing)

                HStack(spacing: 8) {
                    Button {
                        appState.analyse()
                    } label: {
                        Label("Analyse", systemImage: "sparkle.magnifyingglass")
                    }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(!appState.canAnalyse)

                    Button {
                        appState.clear()
                    } label: {
                        Label("Clear", systemImage: "xmark.circle")
                    }
                    .buttonStyle(.bordered)
                    .disabled(appState.prompt.isEmpty && appState.analysis.isIdle)
                }

                analysisBody
            }
            .padding(18)
        }
        .frame(width: 420, height: 560)
        .background(.regularMaterial)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "arrow.triangle.branch")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.tint)
            Text("Wayfinder")
                .font(.headline)
            Spacer()
            Button {
                NSApp.terminate(nil)
            } label: {
                Image(systemName: "power")
            }
            .buttonStyle(.plain)
            .help("Quit Wayfinder")
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 13)
    }

    @ViewBuilder
    private var analysisBody: some View {
        switch appState.analysis {
        case .idle:
            EmptyStateView()
        case .analysing:
            HStack(spacing: 10) {
                ProgressView()
                    .controlSize(.small)
                Text("Analysing")
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 160)
        case .result(let decision):
            RoutingResultView(decision: decision)
        case .failed(let message):
            ErrorView(message: message)
        }
    }
}

private struct EmptyStateView: View {
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 28))
                .foregroundStyle(.secondary)
            Text("Ready")
                .font(.headline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 180)
    }
}

private struct ErrorView: View {
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Analysis failed", systemImage: "exclamationmark.triangle")
                .font(.headline)
                .foregroundStyle(.red)
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
}
