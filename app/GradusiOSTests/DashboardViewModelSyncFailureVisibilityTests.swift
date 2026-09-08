import Foundation
@testable import GradusiOS
import GradusKit
import Testing

// A failed CloudKit read must be distinguishable from a quiet zone.
//
// Both of this app's reads of `GradusZone` used to discard their error --
// `performSync` with `try?`, `performIncrementalSync` with a bare `break` on
// `.failure` -- while CV-6 correctly kept the cached dashboard on screen. The
// combination is what made the Mac's equivalent bug survive to a shipped build:
// a fetch that had never once succeeded looked exactly like data that had not
// changed. `lastSyncFailed` is the signal the header renders; these tests pin
// that it is set on failure, that failure does not disturb cached state, and
// that a later success clears it.
//
// `liveLifecycleNeedsRetry` is deliberately not that signal -- it schedules
// retries and no view reads it. The last test here pins that they are distinct.

private struct ThrowingCloudFetcher: CloudFetcher {
    func fetchAll() async throws -> [ProviderStatus] {
        throw CloudFetcherError.fetchFailed
    }
}

/// Fails the first `failures` reads, then answers. One instance lets a single
/// view model live through a failure and a recovery, which is what the flag has
/// to survive -- and keeps both halves of the test on one `UserDefaults` suite.
private actor FailThenSucceedCloudFetcher: CloudFetcher {
    private var remainingFailures: Int
    private let statuses: [ProviderStatus]

    init(failures: Int, then statuses: [ProviderStatus]) {
        remainingFailures = failures
        self.statuses = statuses
    }

    func fetchAll() async throws -> [ProviderStatus] {
        guard remainingFailures == 0 else {
            remainingFailures -= 1
            throw CloudFetcherError.fetchFailed
        }
        return statuses
    }
}

@MainActor
private func viewModel(fetcher: CloudFetcher, test: String = #function) -> DashboardViewModel {
    let defaults = syncIsolatedDefaults(test)
    defaults.set(true, forKey: DashboardViewModel.syncEnabledKey)
    let model = DashboardViewModel(cache: syncTempCache(), fetcher: fetcher, userDefaults: defaults)
    model.syncEnabled = true
    model.updateAccountStatus(.available)
    return model
}

@MainActor
@Test func aFailedFullSyncIsReportedRatherThanSwallowed() async {
    let model = viewModel(fetcher: ThrowingCloudFetcher())
    #expect(!model.lastSyncFailed)

    let succeeded = await model.sync()

    #expect(!succeeded)
    #expect(model.lastSyncFailed)
}

@MainActor
@Test func aFailedFullSyncLeavesTheCachedDashboardIntact() async {
    let model = viewModel(fetcher: ThrowingCloudFetcher())
    model.allProviders = [makeStatus("cached")]
    model.applyPresentationPreferences()
    let syncedBefore = model.lastSyncedAt

    _ = await model.sync()

    // The whole point of reporting rather than clearing: the user keeps the
    // last-known numbers and is told they are not fresh.
    #expect(model.allProviders.map(\.providerName) == ["cached"])
    #expect(model.providers.map(\.providerName) == ["cached"])
    #expect(model.lastSyncedAt == syncedBefore)
}

@MainActor
@Test func aLaterSuccessfulSyncClearsTheFailureFlag() async {
    let model = viewModel(
        fetcher: FailThenSucceedCloudFetcher(failures: 1, then: [makeStatus("live")])
    )

    let firstAttempt = await model.sync()
    #expect(!firstAttempt)
    #expect(model.lastSyncFailed)

    let succeeded = await model.sync()

    #expect(succeeded)
    #expect(!model.lastSyncFailed)
    #expect(model.providers.map(\.providerName) == ["live"])
}

@MainActor
@Test func aFailedIncrementalSyncReportsTheSameWayAFullOneDoes() async {
    let cache = syncTempCache()
    let fetcher = MockZoneChangesFetcher(outcomes: [.failure])
    let model = makeViewModel(cache: cache, fetcher: fetcher)
    model.allProviders = [makeStatus("cached")]
    model.applyPresentationPreferences()

    await model.handleRemoteNotification()

    #expect(model.lastSyncFailed)
    #expect(model.providers.map(\.providerName) == ["cached"])
}

@MainActor
@Test func anAbsentZoneIsAnAnswerNotAFailure() async {
    let cache = syncTempCache()
    let fetcher = MockZoneChangesFetcher(outcomes: [.zoneNotFound])
    let model = makeViewModel(cache: cache, fetcher: fetcher)
    model.lastSyncFailed = true

    await model.handleRemoteNotification()

    // `GradusZone` is Mac-owned; before its first publish it legitimately does
    // not exist. That is "waiting for first publish", not a read that failed,
    // and conflating the two would put a permanent error in the header of every
    // freshly installed app.
    #expect(!model.lastSyncFailed)
    #expect(model.providers.isEmpty)
}

@MainActor
@Test func theHeaderRefusesToClaimAFreshSyncAfterAFailedRead() {
    let published = Date(timeIntervalSince1970: 1_785_000_000)
    let now = published.addingTimeInterval(120)
    let source = SyncSource(computerName: "dm5mbp", userName: "dave")

    let healthy = SyncStatusLine(source: source, publishedAt: published, now: now)
    #expect(healthy.renderedText == "synced 2m ago · dm5mbp")

    let failed = SyncStatusLine(source: source, publishedAt: published, now: now, refreshFailed: true)
    let text = try? #require(failed.renderedText)
    #expect(text?.hasPrefix("couldn't refresh") == true)
    // The age is kept so the staleness is legible; the computer name is dropped
    // to hold the line to one row.
    #expect(text?.contains("2m ago") == true)
    #expect(text?.contains("dm5mbp") == false)
}

@MainActor
@Test func theHeaderSaysSoEvenWithNothingEverSynced() {
    let line = SyncStatusLine(source: nil, publishedAt: nil, now: Date(), refreshFailed: true)
    #expect(line.renderedText == "couldn't refresh")
}

@MainActor
@Test func turningSyncOffRetiresTheFailureRatherThanFreezingIt() {
    let cache = syncTempCache()
    let model = makeViewModel(cache: cache, fetcher: MockZoneChangesFetcher(outcomes: []))
    model.syncEnabled = true
    model.lastSyncFailed = true

    model.syncEnabled = false

    // Every sync path guards on `syncEnabled`, so nothing would ever clear this
    // again -- the header would keep apologizing for a read the app deliberately
    // stopped attempting.
    #expect(!model.lastSyncFailed)
}
