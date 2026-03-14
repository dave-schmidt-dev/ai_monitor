# History

## 2026-03-14

- Fixed `NameError: name 're' is not defined` crash on startup by adding missing `import re` to `ui.py`.
- Refactored provider card rendering through a shared spec-driven row builder so Claude and Codex use the same metric/reset/pace text pipeline.
- Added UI regression tests to keep shared provider card labels aligned as new providers are added.
- Canonicalized reset date/time formatting across provider strings, including relative, 24-hour, and vendor-specific reset text variants.
- Added normalized reset display fields to `--json` output so scripts can reuse the same canonical formatting as the TUI.

## 2026-03-13

- Added initial PTY-based terminal monitor for Codex and Claude usage.
- Mirrored the core local probing strategy used by `steipete/CodexBar`.
- Added parser tests and basic project documentation.
- Reworked the plain text output into a styled terminal dashboard with progress bars and reset countdowns.
- Added a `monitor` command entrypoint and repo-local launcher script.
- Tightened the dashboard layout with fixed compact card widths to preserve split view and avoid right-edge border wrapping.
- Standardized Claude and Codex dashboard wording around shared 5-hour and 1-week session windows, and removed the unused Codex credits row.
- Added GitHub-ready repo hygiene with a `.gitignore`, richer README documentation, and checked-in screenshots.
- Added an MIT license for public GitHub distribution.
- Kept the last good provider snapshot on transient probe failures so temporary Claude usage errors do not replace the live card immediately.
- Added a `cached` badge so reused provider snapshots are visible in the dashboard.
- Added cached age display so reused provider snapshots show how stale they are.
- Increased the default refresh interval from 60 seconds to 120 seconds while keeping `--interval` as an override.
