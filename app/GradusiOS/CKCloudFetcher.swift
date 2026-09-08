import CloudKit
import Foundation
import GradusKit

/// Read side of the CloudKit seam for iOS (Phase 3) -- takes a full snapshot of
/// every `ProviderStatus` record in the shared `GradusZone` of the private
/// database and maps each via `ProviderStatus(record:)` (malformed/missing
/// `windowsJSON`/`dataJSON` degrade rather than throw there, per CV-3).
public struct CKCloudFetcher: CloudFetcher {
    private let database: CKDatabase
    private let zoneID: CKRecordZone.ID

    public init(database: CKDatabase, zoneID: CKRecordZone.ID) {
        self.database = database
        self.zoneID = zoneID
    }

    /// Authoritative full-zone read of the provider records.
    ///
    /// Deliberately *not* a `CKQuery`. A query with `NSPredicate(value: true)`
    /// requires a QUERYABLE index on the record type's `recordName` system
    /// field, which is untracked server-side state -- the absence of exactly
    /// that index on `DevicePresence` is what left Mac Settings' device list
    /// permanently empty in Production (2026-09-08). Whether `ProviderStatus`
    /// had the index was never determined and no longer matters: a nil-token
    /// zone-changes fetch returns the same complete snapshot, needs no index,
    /// and is the mechanism the incremental path already uses, so both of this
    /// app's reads of `GradusZone` now depend on one server contract.
    public func fetchAll() async throws -> [ProviderStatus] {
        let fetcher = CKZoneChangesFetcher(database: database, zoneID: zoneID)
        switch await fetcher.fetchZoneChanges(sinceToken: nil) {
        case let .success(changed, _, _):
            return changed
        case let .successWithPresence(changed, _, _, _, _):
            return changed
        case .zoneNotFound, .zoneDeleted:
            // `GradusZone` is Mac-owned and recreated idempotently on its next
            // publish (T2a.2); iOS is consumer-only and cannot recreate it. An
            // absent zone is "waiting for first publish", which is an empty
            // snapshot rather than a read failure -- the same reading
            // `performIncrementalSync` gives these two outcomes.
            return []
        case .changeTokenExpired, .failure:
            // `.changeTokenExpired` cannot follow a nil token. Throwing on both
            // keeps a server fault from reaching the caller as "no providers,"
            // which would silently clear the dashboard and its cache.
            throw CloudFetcherError.fetchFailed
        }
    }
}

/// Distinguishes a failed read from an empty zone. `fetchAll()` returning `[]`
/// is a real answer -- the zone exists and holds nothing -- so the failure case
/// needs to be a throw the caller can render differently.
public enum CloudFetcherError: Error {
    case fetchFailed
}
