import Foundation
import Testing

// The triage asked for "badge workflow" coverage. There is no badge workflow:
// `LocalWarningNotificationScheduler` sets title, body and sound and never
// touches `content.badge`, and nothing else in either app produces a badge
// count. The only badge code is `AppDelegate`'s clear-on-foreground path,
// which exists to wipe a stale badge left by an earlier app version, and that
// path is already covered by `AppDelegateTests`.
//
// So the useful test is not a workflow test -- it would assert nothing -- but
// a tripwire that keeps the two halves honest. Today "the badge is always
// clear" holds because nothing ever sets one. If a future change starts
// setting a badge, clear-on-foreground silently becomes wrong (it would wipe a
// live count the moment the user opened the app), and this test is what makes
// that change stop and think instead of shipping quietly.
//
// Uses the same source-scan mechanism as `CloudKitSchemaScopeTests`.

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

@Test func noNotificationContentCarriesABadge() throws {
    let root = appSourceRoot()
    let sources = productionSources(root: root)
    #expect(sources.count > 20, "expected to scan the app's production Swift sources under \(root.path)")

    var scannedANotificationBuilder = false
    for file in sources {
        let contents = try strippingLineComments(String(contentsOf: file, encoding: .utf8))
        guard contents.contains("UNMutableNotificationContent") else { continue }
        scannedANotificationBuilder = true
        #expect(
            !contents.contains(".badge ="),
            """
            \(file.lastPathComponent) sets a notification badge. AppDelegate clears the badge on \
            every foreground transition, so a badge set here would be wiped as soon as the user \
            opened the app. Decide the clear-on-foreground semantics before adding one.
            """
        )
    }
    // The guard above skips files that build no notification content, so it
    // would pass trivially if the scheduler were ever renamed out of the sweep.
    #expect(scannedANotificationBuilder, "expected at least one notification builder in the scan")
}

@Test func theOnlyBadgeCountEverSetIsZero() throws {
    let sources = productionSources(root: appSourceRoot())
    var callSites = 0
    for file in sources {
        let contents = try strippingLineComments(String(contentsOf: file, encoding: .utf8))
        var searchStart = contents.startIndex
        while let range = contents.range(of: "setBadgeCount(", range: searchStart ..< contents.endIndex) {
            callSites += 1
            let argument = contents[range.upperBound...].prefix(1)
            #expect(
                argument == "0",
                "\(file.lastPathComponent) calls setBadgeCount with a non-zero argument"
            )
            searchStart = range.upperBound
        }
    }
    #expect(callSites == 1, "expected exactly the AppDelegate clear-on-foreground call site")
}
