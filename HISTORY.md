# History

## 2026-04-15

- Extracted a shared Safari `Cookies.binarycookies` parser and reused it for both Cursor and Vibe auth cookie flows.
- Added generalized billing-cycle pace logic and wired pace rows for Cursor (`credit pace`) and Vibe (`month pace`).
- Removed the Vibe `pay-as-you-go` row from the dashboard card to reduce visual noise.
- Collapsed provider error cards to a compact single-line message panel.
- Added live keyboard shortcuts: `q` quits immediately and `r` triggers an immediate refresh.
- Added optional `.ai_monitor.json` configuration (`providers`, `interval`, `threshold`) plus `--providers` CLI override filtering.
- Added threshold notifications (macOS `osascript`) with one-shot crossing semantics and automatic reset when recovered.
- Added a `[!]` low-usage badge in provider titles when remaining percentage is below the configured threshold.
- Replaced ~280 lines of hand-rolled ANSI escape code rendering with Python's `rich` library (`Live`, `Panel`, `Table.grid`, custom `PercentageBar` renderable). This eliminates the persistent scrollback buffer growth bug that 6+ prior fix attempts using raw ANSI sequences could not fully resolve.
- Added `rich>=15.0` as the first runtime dependency.
- `--once` mode now prints directly via `Console.print` without entering alt-screen (no flash).
- `--json` mode writes directly to `sys.stdout` with explicit flush (no Rich dependency in JSON path).
- Live interactive mode uses `Live(screen=True, auto_refresh=False)` with manual refresh for precise countdown control.
- Countdown loop uses deadline-based drift correction instead of accumulating `time.sleep(1)` calls.
- Fixed pre-existing `refresh()` closure bug where the inner function referenced the wrong `snapshots` variable from outer scope instead of its `previous` parameter.
- Removed all ANSI constants, old PALETTE dict, and ~30 hand-rolled rendering functions (`write_screen`, `enter_alt_screen`, `leave_alt_screen`, `countdown_sleep`, `_card`, `_merge_columns`, `_progress_bar`, etc.).
- Rewrote test suite from ANSI string assertions to Rich Console capture pattern; test count increased from 29 to 42.
- Fixed missing leading `/` in `aimonitor` alias in `~/.zshrc` that prevented the alias from resolving.
- Reorganized `~/.zshrc` into labeled sections (PATH, Completion, AI/LLM Tools, Projects, System/Infra).
- Updated write_screen tests to document the always-full-clear invariant.

## 2026-04-14

- Replaced absolute-home/alternate-screen repainting with cursor-relative redraw (`cursor-up + clear-to-end`) to keep startup/countdown/refresh animations in-place on terminals that ignore or partially implement those older control paths.
- Added regressions for repaint control-sequence behavior, including multi-line cursor-up redraw between frames.
- Reworked terminal repainting so dashboard updates now clear and redraw in-place via `write_screen(..., repaint=True)`, preventing downward frame accumulation during startup, countdown, and refresh updates.
- Restored live startup spinner timing and live countdown updates after the repaint regression fixes.
- Added regressions for startup animation, countdown tick rendering, and TTY repaint escape behavior.
- Fixed the refresh-loop regression that repeatedly repainted the full dashboard at 10Hz during provider updates; refresh now shows a single in-place `updating` state until the probe batch finishes.
- Switched Copilot monthly reset semantics to UTC midnight (first day of next month, `UTC`) to match GitHub premium reset behavior.
- Added a Copilot `month rem` color progress bar so Copilot remaining usage visually matches the other provider cards.
- Refined the Copilot card to monthly semantics (`month rem`, `month reset`, `month pace`) and switched remaining display to one decimal place.
- Added Copilot monthly pace calculation against expected month progress, plus monthly reset normalization to first-of-month local midnight.
- Updated the live refresh loop to show an explicit `updating …` header state while provider probes are running, then restore the countdown when refresh completes.
- Confirmed Copilot probing remains passive status-line sampling only (no prompt send path that would consume premium requests).
- Improved Gemini probe failures by detecting the CLI "Waiting for authentication..." screen and surfacing a direct sign-in instruction instead of a generic stats-panel parse error.
- Added provider helper tests to cover Gemini authentication-wait detection.
- Fixed Gemini direct quota probing for bundled Gemini CLI installs (for example Homebrew v0.37.x), restoring Flash/Pro usage without relying on `/stats` PTY scraping.
- Fixed Claude usage parsing for compressed single-line panels so 5h/1w percentages and reset fields no longer collapse into one mixed value.
- Hardened Codex probing against transient PTY control-sequence noise and added a startup warmup/retry path so `/status` reliably captures full 5h and weekly limits.
- Added a fourth provider card for GitHub Copilot by probing interactive status-line premium request signals.
- Added Copilot parser coverage and UI coverage so premium request, remaining percentage, and pace rows render consistently with the existing dashboard style.

## 2026-03-14

- Added Gemini CLI support by probing `/stats` and rendering compact Flash and Pro pool cards.
- Reworked the dashboard into a compact two-column grid so three providers still fit in smaller terminal windows.
- Tightened row and card widths to reduce right-edge wrapping in split view.
- Fixed `NameError: name 're' is not defined` crash on startup by adding missing `import re` to `ui.py`.
- Refactored provider card rendering through a shared spec-driven row builder so Claude and Codex use the same metric/reset/pace text pipeline.
- Added UI regression tests to keep shared provider card labels aligned as new providers are added.
- Canonicalized reset date/time formatting across provider strings, including relative, 24-hour, and vendor-specific reset text variants.
- Added normalized reset display fields to `--json` output so scripts can reuse the same canonical formatting as the TUI.
- Replaced Gemini PTY scraping with a direct internal quota probe against the installed Gemini CLI so Gemini cards no longer depend on `/stats` terminal rendering.
- Added a dedicated Claude warmup phase before sending `/usage` to handle folder trust prompts and startup output.
- Added early empty-output detection for all providers so blank PTY responses trigger a retry instead of a parse failure.
- Added debug dump support (`--debug`) that writes raw PTY captures to `/tmp/ai_monitor_*_capture.txt`.
- Added provider regression coverage for Gemini's mixed log-plus-JSON stdout shape.
- Confirmed Claude PTY probing works correctly; current `/usage` failures are a server-side Anthropic API issue returning empty limit data for Team seats, not a transport problem.

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
