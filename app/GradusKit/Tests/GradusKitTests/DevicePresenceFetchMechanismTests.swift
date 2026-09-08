import Foundation
@testable import GradusKit
import Testing

// A tripwire, not a behavior test.
//
// Mac Settings -> Connected Devices was broken in Production from the day it
// shipped. `CKDevicePresenceClient.fetchAll()` read presence with a
// `CKQuery(recordType: "DevicePresence", predicate: NSPredicate(value: true))`,
// and a query like that requires a QUERYABLE index on the record type's
// `recordName` system field. The deployed schema has none, so CloudKit rejected
// every single fetch:
//
//     CKInternalErrorDomain Code=2015
//     "Field 'recordName' is not marked queryable"
//
// The directory store caught the error and rendered an empty list, so the panel
// looked exactly like an idle phone and nobody could tell. iOS was never
// affected because it reads the same zone through
// `CKFetchRecordZoneChangesOperation`, which needs no index at all.
//
// The fix put both platforms on that one mechanism. Nothing in a unit test can
// reproduce the server's rejection -- a fake `CKDatabase` will happily answer a
// query -- so the only way to keep the regression out is to assert on the
// source: the presence path must not reach for `CKQuery` again. If a future
// change needs one, it has to add the Dashboard index and deploy it to
// Production first, and deleting this test is the moment to think about that.

private let presenceClientFileName = "DevicePresenceCloud.swift"

private func appSourceRoot(filePath: String = #filePath) -> URL {
    URL(fileURLWithPath: filePath)
        .deletingLastPathComponent() // GradusKitTests
        .deletingLastPathComponent() // Tests
        .deletingLastPathComponent() // GradusKit
        .deletingLastPathComponent() // app
}

private func isProductionSource(_ url: URL) -> Bool {
    let excludedDirectories: Set = [
        ".build", "build", "DerivedData", "release_candidate", "Pods", "Carthage"
    ]
    for component in url.pathComponents {
        if excludedDirectories.contains(component) {
            return false
        }
        if component.hasSuffix("Tests") {
            return false
        }
    }
    return url.pathExtension == "swift"
}

private func strippingLineComments(_ contents: String) -> String {
    contents
        .components(separatedBy: .newlines)
        .map { line -> Substring in
            guard let marker = line.range(of: "//") else { return line[...] }
            return line[line.startIndex ..< marker.lowerBound]
        }
        .joined(separator: "\n")
}

private func productionSources(root: URL) -> [URL] {
    guard
        let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)
    else { return [] }
    return enumerator.compactMap { $0 as? URL }.filter(isProductionSource)
}

@Test func presenceIsNeverReadWithACloudKitQuery() throws {
    let sources = productionSources(root: appSourceRoot())
    #expect(sources.count > 20, "expected to scan the app's production Swift sources")

    var sawPresenceClient = false
    for file in sources {
        let contents = try strippingLineComments(String(contentsOf: file, encoding: .utf8))
        guard contents.contains(CloudKitConstants.devicePresenceRecordType) else { continue }
        sawPresenceClient = true
        #expect(
            !contents.contains("CKQuery("),
            """
            \(file.lastPathComponent) reads the presence record type with a CKQuery. That needs a \
            QUERYABLE index on `recordName` which the deployed schema does not have, and CloudKit \
            rejects the fetch with CKInternalErrorDomain 2015. Use the nil-token \
            CKFetchRecordZoneChangesOperation path instead, or deploy the index first.
            """
        )
    }
    // Without this the scan would pass trivially if the record type constant
    // were ever inlined or the file renamed out of the sweep.
    #expect(sawPresenceClient, "expected at least one production file referencing the presence record type")
}

@Test func theSharedPresenceClientReadsThroughTheZoneChangesFetcher() throws {
    let sources = productionSources(root: appSourceRoot())
    let client = try #require(
        sources.first { $0.lastPathComponent == presenceClientFileName },
        "\(presenceClientFileName) is gone; the presence read path moved and this tripwire needs to follow it"
    )
    let contents = try strippingLineComments(String(contentsOf: client, encoding: .utf8))
    #expect(
        contents.contains("fetchZoneChanges(sinceToken: nil)"),
        """
        \(presenceClientFileName) no longer takes a full-zone snapshot through the zone-changes \
        fetcher. A nil token is what makes that fetch authoritative rather than incremental, which \
        is what `fetchAll()` promises its caller.
        """
    )
}
