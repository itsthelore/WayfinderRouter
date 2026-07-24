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

struct RoutePreview: Equatable, Identifiable {
  let destinationID: String
  let destinationName: String
  let score: Double
  let recommendation: String
  let executionSummary: String

  var id: String { destinationID }
}

enum RoutePreviewState: Equatable {
  case idle
  case routed(RoutePreview)
  case unavailable(String)
}

enum ConversationRetentionPolicy: String, CaseIterable, Identifiable {
  case thirtyDays
  case ninetyDays
  case forever

  var id: Self { self }

  var title: String {
    switch self {
    case .thirtyDays: "30 days"
    case .ninetyDays: "90 days"
    case .forever: "Forever"
    }
  }

  var days: Int? {
    switch self {
    case .thirtyDays: 30
    case .ninetyDays: 90
    case .forever: nil
    }
  }

  init(days: Int?) {
    switch days {
    case 30: self = .thirtyDays
    case 90: self = .ninetyDays
    default: self = .forever
    }
  }
}

@MainActor
@Observable
final class AppModel {
  var selectedTab: AppTab = .chat
  var draft = ""
  var submittedPrompt: String?
  var privacyPosture: PrivacyPostureOption = .hostedAllowed
  var routePreviewState: RoutePreviewState = .idle
  var threads: [ConversationThreadSnapshot] = []
  var activeThreadID: UUID?
  var persistenceNotice: String?
  var isRestoringConversations = false
  var retentionPolicy: ConversationRetentionPolicy = .forever

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
  private let conversationStore: any ConversationStore
  private let now: () -> Date
  private var hasRestoredConversations = false
  private var draftSaveTask: Task<Void, Never>?

  init(
    conversationStore: any ConversationStore = InMemoryConversationStore(),
    initialPersistenceNotice: String? = nil,
    now: @escaping () -> Date = Date.init
  ) {
    self.conversationStore = conversationStore
    self.persistenceNotice = initialPersistenceNotice
    self.now = now

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

  var activeThread: ConversationThreadSnapshot? {
    guard let activeThreadID else {
      return nil
    }
    return threads.first { $0.id == activeThreadID }
  }

  func restoreConversations() async {
    guard !hasRestoredConversations else {
      return
    }

    hasRestoredConversations = true
    isRestoringConversations = true
    defer { isRestoringConversations = false }

    do {
      threads = try await conversationStore.listThreads()
      let workspace = try await conversationStore.loadWorkspace()
      retentionPolicy = ConversationRetentionPolicy(
        days: workspace.retentionDays
      )
      activeThreadID = workspace.activeThreadID

      if let activeThread {
        draft = activeThread.draft
        restorePreview(from: activeThread)
      } else {
        activeThreadID = nil
        draft = workspace.draft
      }

      await applyRetentionPolicy()
    } catch {
      persistenceNotice =
        "Wayfinder could not restore saved conversations. New chats remain available."
    }
  }

  func previewRoute() async {
    let prompt = draft.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !prompt.isEmpty else {
      routePreviewState = .unavailable("Enter a message to preview its route.")
      return
    }

    submittedPrompt = prompt

    var storedReceipt: StoredRouteReceipt?
    var messageStatus = ConversationMessageStatus.completed

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
        await persistTurn(
          prompt: prompt,
          status: .failed,
          receipt: nil
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
      storedReceipt = StoredRouteReceipt(
        destinationID: destination.id,
        destinationName: destination.displayName,
        score: plan.score,
        recommendation: plan.recommendation,
        executionSummary: destination.boundaryLabel
      )
    } catch {
      routePreviewState = .unavailable(
        "Wayfinder could not calculate this route. Try a shorter message."
      )
      messageStatus = .failed
    }

    await persistTurn(
      prompt: prompt,
      status: messageStatus,
      receipt: storedReceipt
    )
  }

  func clearPreview() {
    routePreviewState = .idle
  }

  func startNewChat() async {
    await persistActiveDraft()
    draft = ""
    submittedPrompt = nil
    routePreviewState = .idle
    activeThreadID = nil
    selectedTab = .chat
    await persistWorkspace()
  }

  func selectThread(id: UUID) async {
    guard id != activeThreadID else {
      selectedTab = .chat
      return
    }

    await persistActiveDraft()

    do {
      guard let thread = try await conversationStore.thread(id: id) else {
        await refreshThreads()
        return
      }

      activeThreadID = id
      draft = thread.draft
      restorePreview(from: thread)
      selectedTab = .chat
      await persistWorkspace()
    } catch {
      persistenceNotice = "Wayfinder could not open that conversation."
    }
  }

  func saveDraft() async {
    if activeThreadID == nil {
      await persistWorkspace()
    } else {
      await persistActiveDraft()
    }
  }

  func scheduleDraftSave() {
    draftSaveTask?.cancel()
    draftSaveTask = Task { [weak self] in
      try? await Task.sleep(for: .milliseconds(350))
      guard !Task.isCancelled else {
        return
      }
      await self?.saveDraft()
    }
  }

  func setRetentionPolicy(_ policy: ConversationRetentionPolicy) async {
    retentionPolicy = policy
    await persistWorkspace()
    await applyRetentionPolicy()
  }

  func exportConversations() async -> Data? {
    do {
      return try await conversationStore.exportData()
    } catch {
      persistenceNotice = "Wayfinder could not prepare the conversation export."
      return nil
    }
  }

  func deleteThread(id: UUID) async {
    do {
      try await conversationStore.deleteThread(id: id)

      if activeThreadID == id {
        activeThreadID = nil
        draft = ""
        submittedPrompt = nil
        routePreviewState = .idle
      }

      await refreshThreads()
      await persistWorkspace()
    } catch {
      persistenceNotice = "Wayfinder could not delete that conversation."
    }
  }

  func deleteAllThreads() async {
    do {
      try await conversationStore.deleteAll()
      threads = []
      activeThreadID = nil
      draft = ""
      submittedPrompt = nil
      routePreviewState = .idle
      await persistWorkspace()
    } catch {
      persistenceNotice = "Wayfinder could not clear saved conversations."
    }
  }

  private func persistTurn(
    prompt: String,
    status: ConversationMessageStatus,
    receipt: StoredRouteReceipt?
  ) async {
    let timestamp = now()
    let message = ConversationMessageSnapshot(
      id: UUID(),
      role: .user,
      content: prompt,
      createdAt: timestamp,
      status: status,
      routeReceipt: receipt
    )

    var thread: ConversationThreadSnapshot
    if let activeThread {
      thread = activeThread
      thread.updatedAt = timestamp
      thread.messages.append(message)
      thread.draft = ""
    } else {
      thread = ConversationThreadSnapshot(
        id: UUID(),
        title: ConversationThreadSnapshot.title(for: prompt),
        createdAt: timestamp,
        updatedAt: timestamp,
        messages: [message],
        draft: ""
      )
      activeThreadID = thread.id
    }

    draft = ""

    do {
      try await conversationStore.save(thread: thread)
      await refreshThreads()
      await persistWorkspace()
    } catch {
      persistenceNotice =
        "This turn is visible now, but Wayfinder could not save it."
      upsertInMemory(thread)
    }
  }

  private func persistActiveDraft() async {
    guard var thread = activeThread else {
      return
    }

    thread.draft = draft
    thread.updatedAt = now()

    do {
      try await conversationStore.save(thread: thread)
      upsertInMemory(thread)
    } catch {
      persistenceNotice = "Wayfinder could not save the current draft."
    }
  }

  private func persistWorkspace() async {
    let workspace = ConversationWorkspaceSnapshot(
      activeThreadID: activeThreadID,
      draft: activeThreadID == nil ? draft : "",
      retentionDays: retentionPolicy.days,
      updatedAt: now()
    )

    do {
      try await conversationStore.save(workspace: workspace)
    } catch {
      persistenceNotice = "Wayfinder could not save the current draft."
    }
  }

  private func refreshThreads() async {
    do {
      threads = try await conversationStore.listThreads()
    } catch {
      persistenceNotice = "Wayfinder could not refresh saved conversations."
    }
  }

  private func applyRetentionPolicy() async {
    guard let days = retentionPolicy.days else {
      return
    }

    let cutoff = now().addingTimeInterval(
      -TimeInterval(days * 24 * 60 * 60)
    )

    do {
      _ = try await conversationStore.pruneThreads(olderThan: cutoff)
      await refreshThreads()

      if let activeThreadID,
        !threads.contains(where: { $0.id == activeThreadID })
      {
        self.activeThreadID = nil
        draft = ""
        submittedPrompt = nil
        routePreviewState = .idle
        await persistWorkspace()
      }
    } catch {
      persistenceNotice = "Wayfinder could not apply conversation retention."
    }
  }

  private func upsertInMemory(_ thread: ConversationThreadSnapshot) {
    threads.removeAll { $0.id == thread.id }
    threads.append(thread)
    threads.sort {
      if $0.updatedAt == $1.updatedAt {
        return $0.id.uuidString < $1.id.uuidString
      }
      return $0.updatedAt > $1.updatedAt
    }
  }

  private func restorePreview(from thread: ConversationThreadSnapshot) {
    guard let message = thread.messages.last else {
      submittedPrompt = nil
      routePreviewState = .idle
      return
    }

    submittedPrompt = message.content

    if let receipt = message.routeReceipt {
      routePreviewState = .routed(
        RoutePreview(
          destinationID: receipt.destinationID,
          destinationName: receipt.destinationName,
          score: receipt.score,
          recommendation: receipt.recommendation,
          executionSummary: receipt.executionSummary
        )
      )
    } else if message.status == .failed {
      routePreviewState = .unavailable(
        "Wayfinder could not calculate a route for this message."
      )
    } else {
      routePreviewState = .idle
    }
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
