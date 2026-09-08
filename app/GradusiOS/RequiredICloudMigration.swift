import Foundation

/// One-way migration from the legacy `iCloudSyncEnabled` boolean to the
/// `RequiredICloudMode` it was replaced by, plus the defaults keys that
/// migration owns.
///
/// Lives in its own file rather than beside `DashboardViewModel`: it is a
/// self-contained defaults transform that the view model calls once at init,
/// and keeping it inline had `DashboardViewModel.swift` sitting exactly on
/// SwiftLint's 400-line limit, where any addition to the view model itself
/// became a file-length violation.
enum RequiredICloudMigration {
    static let modeKey = "requiredICloudMode"
    static let versionKey = "requiredICloudModeVersion"
    static let currentVersion = 1

    static func migrate(
        defaults: UserDefaults,
        legacyKey: String,
        writeMode: (UserDefaults, RequiredICloudMode) -> Void = { defaults, mode in
            defaults.set(mode.rawValue, forKey: modeKey)
            defaults.set(currentVersion, forKey: versionKey)
        }
    ) -> RequiredICloudMode {
        let mode: RequiredICloudMode = if let stored = defaults.object(forKey: modeKey) as? String,
                                          let storedMode = RequiredICloudMode(rawValue: stored) {
            // The new authority wins if both generations are present. Re-write
            // its version before removing the legacy value so a partial write
            // remains safely re-runnable.
            storedMode
        } else if defaults.object(forKey: legacyKey) == nil {
            .confirmed
        } else {
            defaults.bool(forKey: legacyKey) ? .confirmed : .awaitingConfirmation
        }
        writeMode(defaults, mode)
        guard let committed = defaults.object(forKey: modeKey) as? String,
              RequiredICloudMode(rawValue: committed) == mode,
              defaults.integer(forKey: versionKey) == currentVersion
        else { return mode }
        defaults.removeObject(forKey: legacyKey)
        return mode
    }
}
