import Foundation
import GradusKit
@testable import GradusMac
import Testing

@MainActor
private final class ObserverAgentService: BackgroundAgentServicing {
    var registration: BackgroundAgentRegistration

    init(registration: BackgroundAgentRegistration = .enabled) {
        self.registration = registration
    }

    func register() throws {
        registration = .enabled
    }

    func unregister() throws {
        registration = .notRegistered
    }
}

@Suite("BackgroundAgentStatusObserverTests")
@MainActor
struct BackgroundAgentStatusObserverTests {
    @Test func terminalStatusClearsCollectingAfterSnapshotDeliveryWithoutPublishing() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let suite = "com.zerodelta.gradus.mac.tests.statusObserver.terminal"
        let snapshotTime = Date(timeIntervalSince1970: 1_800_000_000)
        var now = snapshotTime
        let setup = try #require(fixture.statusViewModel(
            statusURL: fixture.statusURL, suite: suite, now: { now }
        ))
        defer { removeScratchDefaultsSuite(suite, using: setup.defaults) }
        let viewModel = setup.viewModel
        let observer = BackgroundAgentStatusObserver(
            statusFileURL: fixture.statusURL,
            viewModel: viewModel
        )

        observer.start() // Missing first file is an honest, non-healthy state.
        #expect(await eventually { viewModel.backgroundAgentState == .stale(lastRefresh: nil) })

        try fixture.write(.producerWaiting, sequence: 1)
        #expect(await eventually {
            if case .refreshing = viewModel.backgroundAgentState {
                return true
            }
            return false
        })

        viewModel.apply(SnapshotPayload(
            schemaVersion: supportedSchemaVersion,
            updatedAt: ISO8601DateFormatter().string(from: snapshotTime),
            providers: []
        ))
        #expect({
            if case .refreshing = viewModel.backgroundAgentState {
                true
            } else {
                false
            }
        }())

        try fixture.write(.succeeded, sequence: 2)
        #expect(await eventually {
            viewModel.backgroundAgentState == .running(lastRefresh: snapshotTime)
        })
        observer.stop()
    }

    @Test func productionInitializerRefreshesOnlyBackgroundAgentPresentation() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let suite = "com.zerodelta.gradus.mac.tests.statusObserver.productionInitializer"
        let snapshotTime = Date(timeIntervalSince1970: 1_800_000_000)
        let setup = try #require(fixture.statusViewModel(
            statusURL: fixture.statusURL, suite: suite, now: { snapshotTime }
        ))
        defer { removeScratchDefaultsSuite(suite, using: setup.defaults) }
        let viewModel = setup.viewModel
        let payload = SnapshotPayload(
            schemaVersion: supportedSchemaVersion,
            updatedAt: ISO8601DateFormatter().string(from: snapshotTime),
            providers: [provider("Codex", ok: true, error: nil)]
        )
        viewModel.apply(payload)
        viewModel.confirmRequiredICloud()
        _ = try #require(viewModel.cloudSyncDidStart())
        let originalProviders = viewModel.providers
        let originalUpdatedAt = viewModel.updatedAt
        let originalSyncState = viewModel.syncState
        let observer = BackgroundAgentStatusObserver(
            statusFileURL: fixture.statusURL,
            viewModel: viewModel
        )

        observer.start()
        try fixture.write(.producerWaiting, sequence: 1)
        #expect(await eventually {
            if case .refreshing = viewModel.backgroundAgentState {
                return true
            }
            return false
        })
        #expect(viewModel.providers == originalProviders)
        #expect(viewModel.updatedAt == originalUpdatedAt)
        #expect(viewModel.syncState == originalSyncState)
        observer.stop()
    }

    @Test func failureCancellationAndClockStalenessRefreshTheViewModelIndependently() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let suite = "com.zerodelta.gradus.mac.tests.statusObserver.staleness"
        let snapshotTime = Date(timeIntervalSince1970: 1_800_000_000)
        var now = snapshotTime
        let setup = try #require(fixture.statusViewModel(
            statusURL: fixture.statusURL, suite: suite, now: { now }
        ))
        defer { removeScratchDefaultsSuite(suite, using: setup.defaults) }
        let viewModel = setup.viewModel
        let observer = fixture.observer(staleInterval: 0.02) {
            viewModel.refreshBackgroundAgentState()
        }

        viewModel.apply(SnapshotPayload(
            schemaVersion: supportedSchemaVersion,
            updatedAt: ISO8601DateFormatter().string(from: snapshotTime),
            providers: []
        ))
        observer.start()
        try fixture.write(.failed, sequence: 1)
        #expect(await eventually {
            viewModel.backgroundAgentState == .stale(lastRefresh: snapshotTime)
        })

        try fixture.write(.cancelled, sequence: 2)
        #expect(await eventually {
            viewModel.backgroundAgentState == .stale(lastRefresh: snapshotTime)
        })

        try fixture.write(.succeeded, sequence: 3)
        #expect(await eventually {
            viewModel.backgroundAgentState == .running(lastRefresh: snapshotTime)
        })

        now = snapshotTime.addingTimeInterval(BackgroundAgentStatusResolver.staleAfter + 1)
        #expect(await eventually(timeoutNanoseconds: 500_000_000) {
            viewModel.backgroundAgentState == .stale(lastRefresh: snapshotTime)
        })
        observer.stop()
    }

    @Test func handlesInvalidAndAtomicReplacementThenStopsAndRestartsWithoutLateCallbacks() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        var observedPhases: [BackgroundAgentStatusFile.Phase?] = []
        let observer = fixture.observer(staleInterval: 10) {
            observedPhases.append(fixture.status())
        }

        observer.start()
        #expect(await eventually { observedPhases.count == 1 })
        #expect(observedPhases == [nil])

        try Data("not status JSON".utf8).write(to: fixture.statusURL, options: .atomic)
        #expect(await eventually { observedPhases.count >= 2 })
        #expect(observedPhases.last.flatMap(\.self) == nil)

        try fixture.write(.producerWaiting, sequence: 1)
        #expect(await eventually { observedPhases.contains(.producerWaiting) })
        try fixture.write(.succeeded, sequence: 2)
        #expect(await eventually { observedPhases.contains(.succeeded) })

        observer.stop()
        let stoppedAt = observedPhases.count
        try fixture.write(.cancelled, sequence: 3)
        try await Task.sleep(nanoseconds: 150_000_000)
        #expect(observedPhases.count == stoppedAt)

        observer.start()
        #expect(await eventually { observedPhases.count > stoppedAt })
        #expect(observedPhases.last == .cancelled)
        observer.stop()
    }

    @Test func stopCancelsAMissingDirectoryRetry() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("GradusStatusObserver-\(UUID().uuidString)", isDirectory: true)
        let statusURL = root.appendingPathComponent("late/agent-status.json")
        defer { try? FileManager.default.removeItem(at: root) }
        var callbacks = 0
        let observer = BackgroundAgentStatusObserver(
            statusFileURL: statusURL,
            retryDelay: 0.02,
            coalescingDelay: 0.001,
            staleRefreshInterval: 10
        ) { callbacks += 1 }

        observer.start()
        observer.stop()
        try FileManager.default.createDirectory(
            at: statusURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try Data("{}".utf8).write(to: statusURL)
        try await Task.sleep(nanoseconds: 100_000_000)
        #expect(callbacks == 0)

        observer.start()
        #expect(await eventually { callbacks == 1 })
        observer.stop()
    }

    @Test func directoryReplacementReopensWatchForSubsequentStatusUpdates() async throws {
        let fixture = try Fixture()
        let replacedDirectory = fixture.directory.appendingPathExtension("replaced")
        defer {
            fixture.remove()
            try? FileManager.default.removeItem(at: replacedDirectory)
        }
        var observedPhases: [BackgroundAgentStatusFile.Phase?] = []
        let observer = fixture.observer(staleInterval: 10) {
            observedPhases.append(fixture.status())
        }

        observer.start()
        #expect(await eventually { observedPhases == [nil] })
        try FileManager.default.moveItem(at: fixture.directory, to: replacedDirectory)
        try await Task.sleep(nanoseconds: 30_000_000)
        try FileManager.default.createDirectory(
            at: fixture.directory, withIntermediateDirectories: true
        )
        try fixture.write(.producerWaiting, sequence: 1)

        #expect(await eventually(timeoutNanoseconds: 1_500_000_000) {
            observedPhases.contains(.producerWaiting)
        })
        try fixture.write(.succeeded, sequence: 2)
        #expect(await eventually { observedPhases.contains(.succeeded) })
        observer.stop()
    }

    @Test func rapidStopStartRejectsQueuedCallbacksFromPreviousGeneration() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        var observedPhases: [BackgroundAgentStatusFile.Phase?] = []
        let observer = BackgroundAgentStatusObserver(
            statusFileURL: fixture.statusURL,
            retryDelay: 0.02,
            coalescingDelay: 0.08,
            staleRefreshInterval: 10
        ) { observedPhases.append(fixture.status()) }

        observer.start()
        #expect(await eventually { observedPhases == [nil] })
        try fixture.write(.producerWaiting, sequence: 1)
        try await Task.sleep(nanoseconds: 20_000_000)
        observer.stop()
        observer.start()
        #expect(await eventually { observedPhases.contains(.producerWaiting) })
        let restartedCount = observedPhases.count
        try await Task.sleep(nanoseconds: 150_000_000)
        #expect(observedPhases.count == restartedCount)
        observer.stop()
    }

    @Test func siblingWritesAreFilteredWhileStaleTimerStillRefreshes() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        var callbacks = 0
        let observer = fixture.observer(staleInterval: 0.12) { callbacks += 1 }

        observer.start()
        #expect(await eventually { callbacks == 1 })
        let siblingURL = fixture.directory.appendingPathComponent("unrelated.json")
        try Data("sibling".utf8).write(to: siblingURL, options: .atomic)
        try await Task.sleep(nanoseconds: 70_000_000)
        #expect(callbacks == 1)
        #expect(await eventually(timeoutNanoseconds: 300_000_000) { callbacks == 2 })
        observer.stop()
    }

    private func eventually(
        timeoutNanoseconds: UInt64 = 1_000_000_000,
        _ condition: @escaping @MainActor () -> Bool
    ) async -> Bool {
        let interval: UInt64 = 10_000_000
        var elapsed: UInt64 = 0
        while elapsed < timeoutNanoseconds {
            if condition() {
                return true
            }
            try? await Task.sleep(nanoseconds: interval)
            elapsed += interval
        }
        return condition()
    }
}

@MainActor
private final class Fixture {
    let directory: URL
    let statusURL: URL

    init() throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("GradusStatusObserver-\(UUID().uuidString)", isDirectory: true)
        statusURL = directory.appendingPathComponent(BackgroundAgentStatusObserver.canonicalFilename)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    func remove() {
        try? FileManager.default.removeItem(at: directory)
    }

    func write(_ phase: BackgroundAgentStatusFile.Phase, sequence: Int) throws {
        let status = BackgroundAgentStatusFile(
            phase: phase,
            health: .normal,
            sequence: sequence,
            updatedAt: "2027-01-15T08:00:00Z"
        )
        try JSONEncoder().encode(status).write(to: statusURL, options: .atomic)
    }

    func status() -> BackgroundAgentStatusFile.Phase? {
        guard let data = try? Data(contentsOf: statusURL) else { return nil }
        return try? JSONDecoder().decode(BackgroundAgentStatusFile.self, from: data).phase
    }

    func observer(
        staleInterval: TimeInterval,
        onChange: @escaping @MainActor @Sendable () -> Void
    ) -> BackgroundAgentStatusObserver {
        BackgroundAgentStatusObserver(
            statusFileURL: statusURL,
            retryDelay: 0.02,
            coalescingDelay: 0.001,
            staleRefreshInterval: staleInterval,
            onChange: onChange
        )
    }

    func statusViewModel(
        statusURL: URL,
        suite: String,
        now: @escaping @MainActor () -> Date
    ) -> (viewModel: PublisherViewModel, defaults: UserDefaults)? {
        guard let defaults = scratchDefaults(suite) else { return nil }
        let service = ObserverAgentService()
        let manager = BackgroundAgentManager(
            service: service,
            statusFileURL: statusURL,
            fullDiskAccessTargetURL: URL(fileURLWithPath: "/nonexistent/Gradus.app"),
            now: now,
            openURL: { _ in },
            revealInFinder: { _ in }
        )
        return (PublisherViewModel(defaults: defaults, backgroundAgent: manager), defaults)
    }
}
