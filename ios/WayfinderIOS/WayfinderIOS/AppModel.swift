import Foundation
import Observation
import WayfinderRoutingBridge

enum AppTab: Hashable, CaseIterable {
  case chat
  case threads
  case destinations
  case settings

  var title: String {
    switch self {
    case .chat: "Chat"
    case .threads: "Threads"
    case .destinations: "Destinations"
    case .settings: "Settings"
    }
  }

  var systemImage: String {
    switch self {
    case .chat: "bubble.left.and.bubble.right"
    case .threads: "clock"
    case .destinations: "point.3.connected.trianglepath.dotted"
    case .settings: "gearshape"
    }
  }
}

enum PrivacyPostureOption: String, CaseIterable, Identifiable {
  case onDeviceOnly
  case localDevices
  case hostedAllowed

  var id: Self { self }

  var title: String {
    switch self {
    case .onDeviceOnly: "On-Device Only"
    case .localDevices: "Local Devices"
    case .hostedAllowed: "Hosted Allowed"
    }
  }

  var boundarySummary: String {
    switch self {
    case .onDeviceOnly: "This iPhone or iPad only"
    case .localDevices: "This device and trusted local devices"
    case .hostedAllowed: "On-device, local-network, and hosted destinations"
    }
  }

  var bridgeValue: PrivacyPosture {
    switch self {
    case .onDeviceOnly: .onDeviceOnly
    case .localDevices: .localDevices
    case .hostedAllowed: .hostedAllowed
    }
  }
}

struct RoutePreview: Equatable {
  let destinationID: String
  let destinationName: String
  let score: Double
  let recommendation: String
  let executionSummary: String
}

enum RoutePreviewState: Equatable {
  case idle
  case routed(RoutePreview)
  case unavailable(String)
}

@MainActor
@Observable
final class AppModel {
  var selectedTab: AppTab = .chat
  var draft = ""
  var privacyPosture: PrivacyPostureOption = .hostedAllowed
  var routePreviewState: RoutePreviewState = .idle

  let destinations: [PreviewDestination] = [
    PreviewDestination(
      id: "device-preview",
      displayName: "On-device preview",
      detail: "Routing candidate only",
      routeTier: "local",
      boundary: .onDevice,
      boundaryLabel: "On this device"
    ),
    PreviewDestination(
      id: "hosted-preview",
      displayName: "Hosted preview",
      detail: "Routing candidate only",
      routeTier: "cloud",
      boundary: .hosted,
      boundaryLabel: "Hosted cloud"
    ),
  ]

  private let routingEngine: RoutingEngine

  init() {
    do {
      routingEngine = try RoutingEngine(
        configuration: RoutingConfiguration(
          tiers: [
            RoutingTier(minScore: 0.0, model: "local"),
            RoutingTier(minScore: 0.1, model: "cloud"),
          ]
        )
      )
    } catch {
      fatalError("The bundled routing configuration is invalid: \(error)")
    }
  }

  var canPreviewRoute: Bool {
    !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
  }

  func previewRoute() {
    let prompt = draft.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !prompt.isEmpty else {
      routePreviewState = .unavailable("Enter a message to preview its route.")
      return
    }

    do {
      let plan = try routingEngine.plan(
        request: RoutingRequest(
          schemaVersion: 1,
          requestId: UUID().uuidString,
          prompt: prompt,
          privacyPosture: privacyPosture.bridgeValue,
          requirements: RoutingRequirements(
            contextTokens: nil,
            imageInput: false,
            tools: false,
            streaming: true
          )
        ),
        candidates: destinations.map(\.bridgeSnapshot)
      )

      guard
        let selectedID = plan.selectedDestinationId,
        let destination = destinations.first(where: { $0.id == selectedID })
      else {
        routePreviewState = .unavailable(
          "No preview destination is eligible under \(privacyPosture.title)."
        )
        return
      }

      routePreviewState = .routed(
        RoutePreview(
          destinationID: destination.id,
          destinationName: destination.displayName,
          score: plan.score,
          recommendation: plan.recommendation,
          executionSummary: destination.boundaryLabel
        )
      )
    } catch {
      routePreviewState = .unavailable(
        "Wayfinder could not calculate this route. Try a shorter message."
      )
    }
  }

  func clearPreview() {
    routePreviewState = .idle
  }
}

struct PreviewDestination: Identifiable, Hashable {
  let id: String
  let displayName: String
  let detail: String
  let routeTier: String
  let boundary: ExecutionBoundary
  let boundaryLabel: String

  var bridgeSnapshot: DestinationSnapshot {
    DestinationSnapshot(
      id: id,
      providerId: "preview",
      modelId: id,
      displayName: displayName,
      routeTier: routeTier,
      executionBoundary: boundary,
      readiness: .ready,
      billingClass: boundary == .onDevice ? .onDevice : .unknown,
      contextWindow: 32_768,
      capabilities: DestinationCapabilities(
        text: true,
        streaming: true,
        imageInput: false,
        tools: false
      ),
      automaticEligible: true
    )
  }
}
