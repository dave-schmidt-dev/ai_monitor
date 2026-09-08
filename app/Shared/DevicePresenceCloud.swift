import CloudKit
import Foundation
import GradusKit

public typealias DevicePresenceRecordSaver = @Sendable (
    CKRecord, CKModifyRecordsOperation.RecordSavePolicy
) async throws -> CKRecord

/// CloudKit transport for the typed private-zone presence record. Provider
/// records use their own mapper and never pass through this type.
public struct CKDevicePresenceClient: DevicePresenceClient {
    public static let upsertSavePolicy: CKModifyRecordsOperation.RecordSavePolicy = .changedKeys

    private let database: CKDatabase?
    private let zoneID: CKRecordZone.ID
    private let saveRecord: DevicePresenceRecordSaver

    public init(
        database: CKDatabase, zoneID: CKRecordZone.ID,
        saveRecord: DevicePresenceRecordSaver? = nil
    ) {
        self.database = database
        self.zoneID = zoneID
        self.saveRecord = saveRecord ?? { record, policy in
            try await Self.modify(record: record, in: database, savePolicy: policy)
        }
    }

    init(zoneID: CKRecordZone.ID, saveRecord: @escaping DevicePresenceRecordSaver) {
        database = nil
        self.zoneID = zoneID
        self.saveRecord = saveRecord
    }

    public func upsert(_ presence: DevicePresence) async throws {
        let record = try presence.toCKRecord(zoneID: zoneID)
        guard record.recordType == CloudKitConstants.devicePresenceRecordType else {
            throw DevicePresenceMappingError.wrongRecordType
        }
        _ = try await saveRecord(record, Self.upsertSavePolicy)
    }

    public func delete(installationID: String) async throws {
        guard let database else { throw DevicePresenceClientError.databaseUnavailable }
        let probe = DevicePresence(installationID: installationID, displayName: .iPhone, expiresAt: .distantFuture)
        let recordID = try probe.toCKRecord(zoneID: zoneID).recordID
        _ = try await database.deleteRecord(withID: recordID)
    }

    /// Authoritative full-zone read of the presence records.
    ///
    /// Deliberately *not* a `CKQuery`. A query with `NSPredicate(value: true)`
    /// requires a QUERYABLE index on the record type's `recordName` system
    /// field, and the deployed Production schema has none, so every fetch came
    /// back `CKInternalErrorDomain 2015 "Field 'recordName' is not marked
    /// queryable"` and Mac Settings rendered a permanently empty device list
    /// (2026-09-08). A nil-token zone-changes fetch returns the same complete
    /// snapshot, needs no index, and is the mechanism iOS already reads
    /// presence with -- one path for both platforms under INV-9.
    public func fetchAll() async throws -> [DevicePresence] {
        guard let database else { throw DevicePresenceClientError.databaseUnavailable }
        let fetcher = CKZoneChangesFetcher(database: database, zoneID: zoneID)
        switch await fetcher.fetchZoneChanges(sinceToken: nil) {
        case let .successWithPresence(_, _, changedPresence, _, _):
            return changedPresence
        case .success:
            return []
        case .zoneNotFound, .zoneDeleted:
            // `GradusZone` is Mac-owned and created idempotently. Before the
            // first publish it legitimately does not exist, which is an empty
            // directory rather than a read failure.
            return []
        case .changeTokenExpired, .failure:
            // `.changeTokenExpired` cannot follow a nil token; treating it as a
            // failure keeps the caller from reading a server fault as "no
            // devices," which is the bug this method used to have.
            throw DevicePresenceClientError.fetchFailed
        }
    }

    public func subscribe() async throws {
        guard let database else { throw DevicePresenceClientError.databaseUnavailable }
        let subscription = CKRecordZoneSubscription(
            zoneID: zoneID,
            subscriptionID: CloudKitConstants.devicePresenceSubscriptionID
        )
        let info = CKSubscription.NotificationInfo()
        info.shouldSendContentAvailable = true
        subscription.notificationInfo = info
        _ = try await database.save(subscription)
    }

    private static func modify(
        record: CKRecord, in database: CKDatabase,
        savePolicy: CKModifyRecordsOperation.RecordSavePolicy
    ) async throws -> CKRecord {
        try await withCheckedThrowingContinuation { continuation in
            let operation = CKModifyRecordsOperation(recordsToSave: [record], recordIDsToDelete: nil)
            operation.savePolicy = savePolicy
            operation.isAtomic = false

            var recordResult: Result<CKRecord, Error>?
            operation.perRecordSaveBlock = { recordID, result in
                guard recordID == record.recordID else { return }
                recordResult = result
            }
            operation.modifyRecordsResultBlock = { operationResult in
                if let recordResult {
                    continuation.resume(with: recordResult)
                } else {
                    switch operationResult {
                    case .success:
                        continuation.resume(throwing: DevicePresenceClientError.missingSaveResult)
                    case let .failure(error):
                        continuation.resume(throwing: error)
                    }
                }
            }
            database.add(operation)
        }
    }
}

public enum DevicePresenceClientError: Error, Equatable {
    case databaseUnavailable
    case missingSaveResult
    case fetchFailed
}
