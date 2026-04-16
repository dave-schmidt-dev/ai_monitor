# History

## 2026-04-16 (session 6)

- **Auth fix actions**: When a provider reports an auth error, the dashboard card now shows `auth error — press [N] to fix` instead of the raw error text, and the footer gains `[N] fix <Name>` entries. Pressing the number key opens a new Terminal.app window (CLI auth: `claude login`, `codex login`, `gemini`, `gh auth login`) or the default browser (web auth: Cursor, Vibe). Non-auth errors are unchanged.
- Added `AUTH_ACTIONS` table, `_is_auth_error()`, `_build_fix_actions()`, `_launch_fix()` to `__main__.py`.
- Added `auth_fix_key` parameter to `build_provider_panel()` and `fix_actions` parameter to `build_dashboard()` in `ui.py`.
- 25 new tests covering auth detection, fix action mapping, launch behavior, panel CTA rendering, and footer hints. 95 tests pass.

## 2026-04-16 (session 5)

- **PTY removal**: deleted all PTY-based provider classes (`CodexProvider`, `ClaudeProvider`, `GeminiProvider`, `CopilotProvider`), `pty_session.py`, and the 3 PTY helpers (`TRUST_PROMPTS`, `_is_empty_or_echo`, `_is_terminal_probe_noise`). HTTP providers are now the only probes.
- **`parsing.py` gutted**: removed all parse functions (`parse_codex_status`, `parse_claude_status`, `parse_gemini_status`, `parse_copilot_status`) and their supporting helpers. File is now ~92 lines of dataclasses only.
- **`GeminiHttpProvider` static methods**: migrated `_find_bucket`, `_percent_from_fraction`, `_read_gemini_account_email` directly onto `GeminiHttpProvider`; replaced `_reset_from_iso` (relative countdown) with `_format_reset_time` (absolute local timestamp) to match all other providers.
- **`CopilotHttpProvider._monthly_reset_label`**: added to `CopilotHttpProvider` and removed the cross-class `CopilotProvider._monthly_reset_label()` call.
- **`__main__.py` simplified**: removed `--compare` flag, `_build_compare_table()`, and all `if compare:` branches; `initialize_providers()` no longer has a `compare` param; provider names drop `[HTTP]` suffix.
- **`source` tag**: changed default in `fetch_provider_snapshot` from `"cli"` to `"api"`; static init errors also tag `source="api"`.
- **Tests**: deleted `test_parsing.py` (tested deleted parse functions); removed 5 PTY-specific test methods from `test_providers.py`; rewrote `test_copilot_monthly_reset_label_uses_local_time` to use `CopilotHttpProvider`.
- Net reduction: ~1,500 lines removed. 70 tests pass.

## 2026-04-16 (session 4)

- **HTTP API probes**: Added direct HTTP provider classes for all 4 PTY-based providers (`CopilotHttpProvider`, `CodexHttpProvider`, `ClaudeHttpProvider`, `GeminiHttpProvider`). Each calls the provider's own REST API using locally cached auth tokens/cookies, returning the same status dataclass as the PTY provider.
  - Copilot: `gh auth token` → GitHub internal API (`/copilot_internal/user`)
  - Codex: `~/.codex/auth.json` → OpenAI wham usage API
  - Claude: Safari cookies (`sessionKey`, `cf_clearance`, `lastActiveOrg`) → `claude.ai/api/organizations/{org_id}/usage`
  - Gemini: `~/.gemini/oauth_creds.json` (with auto-refresh) → `cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`
- **Shared helpers**: `_http_json()` (stdlib urllib wrapper, maps HTTP errors to `ProbeFailure`) and `_format_reset_time()` (unifies ISO/epoch-sec/epoch-ms → `"Resets Mon DD at HH:MM AM/PM"` across providers).
- **`--compare` flag**: runs PTY and HTTP probes side-by-side, prints a delta table after `--once` output.
- **Transient error patterns**: `_is_transient_probe_error()` now recognizes `"http 429"`, `"http 502"`, `"http 503"`, `"token expired"` so cached snapshots survive HTTP rate-limit storms the same way PTY errors do.
- **`fetch_provider_snapshot()` source param**: accepts `source="api"` so HTTP snapshots are tagged differently from PTY ones.
- Added 20 unit tests for the new helpers and all 4 HTTP providers.

## 2026-04-16 (session 3)

- **Dashboard label consistency**: all provider window labels shortened to 2 chars (`5h`, `1w`, `mo`, `fl`, `pr`). Gemini `flash`/`pro` → `fl`/`pr`; Copilot/Cursor/Vibe `1mo` → `mo`; Cursor `plan` → `pl`. Column 1 pinned to `max_width=2` in the table layout.
- **Percent format**: all providers now display integer-only percentages (`99%` not `99.1%`). `_format_percent_value` simplified; Copilot/Cursor/Vibe previously used `.1f` format which consumed an extra column character and shortened their progress bars.
- **Pace format**: `_billing_cycle_pace_label` now returns integer point labels (`over -5pt`) matching `_pace_label`, eliminating the 2-char discrepancy that shortened Copilot/Vibe/Cursor bars.
- **Progress bar width standardised**: reset (col 4) and pace (col 5) columns pinned to `min_width=12, max_width=12`. Combined with the label and percent fixes, all provider bars are now the same width regardless of content variation between providers.
- **Empty/depleted view**: when a provider has no usable capacity (Codex/Claude: EITHER 5h or 1w at 0%; Copilot/Cursor/Vibe: mo at 0%; Gemini: BOTH fl AND pr at 0%), all rows replace bar+pace with `0%  until <reset_time>`. Non-depleted windows in a blocked provider show the blocking window's reset time. Implemented via `_is_empty_window`, `_provider_is_empty`, and `_add_empty_view`; 9 tests cover all provider-specific trigger logic.
- **Empty bar colour**: empty bar segments (`░`) now use `bar.empty` (`color(244)`, mid-light grey) instead of `shadow` (`color(239)`, dark grey), clearly distinguishing unused capacity from used.
- **Quit during refresh bug fixed**: the refresh-phase polling loop now uses `select.select` with a 0.12s timeout to check for `q`/`Q` keypresses, matching the countdown phase. Previously `time.sleep(0.12)` blocked keyboard input until the fetch completed.
- 64 tests pass.

## 2026-04-16 (session 2)

- Dashboard UI overhaul — all providers now use a unified 5-column row layout (`label | % | bar | reset | pace`) so progress bars align visually across all panels in the 2-column grid.
- Collapsed windowed providers (Claude, Codex, Gemini) from 2 rows per window to 1: reset time and pace indicator now appear inline on the same row as the usage bar.
- Collapsed monthly providers (Copilot, Cursor, Vibe) from 3 rows to 1: same inline layout.
- Label changes: `"5h session"` → `"5h"`, `"1w session"` → `"1w"`, `"flash pool"` → `"flash"`, `"pro pool"` → `"pro"`, `"month rem"` / `"credit rem"` → `"1mo"` (consistent cycle-length notation across all providers).
- Pace values simplified: `"under pace +15pt"` → `"under +15pt"`, `"over pace -5pt"` → `"over -5pt"`.
- Switched all times to 24h format — AM/PM removed everywhere (display and header).
- Header: day-of-week dropped, `"Refreshing in Xs"` → `"↻ Xs"` with pipe divider, `"Last Updated:"` label kept.
- Footer: `[Ctrl-C] exit · --json --debug` removed, leaving only `[q] quit  [r] refresh`.
- Provider panel borders now use each provider's accent color (pink=Claude, blue=Codex, teal=Gemini, cyan=Copilot, orange=Cursor, amber=Vibe) instead of flat grey.
- Gemini windows given approximate `window_hours` (24h flash, 720h pro) to enable pace calculation.
- Extracted `_pace_style()` helper to eliminate 4 identical if/elif/else pace-color blocks.
- All test assertions updated for new labels, 24h times, and header format; 55 tests pass.

## 2026-04-16

- Fixed keyboard shortcut hints in dashboard footer: replaced invisible `dim text.muted` styling with `Text.assemble` using cyan color on key labels (`[q]`, `[r]`, `[Ctrl-C]`). Color-only styles are correctly stripped by `no_color=True` rendering so ANSI regression tests continue to pass.
- Tightened dashboard header: collapsed into a single line (`AI Usage Monitor | Last Updated: <timestamp> | Refreshing in <N>s`), removed redundant subtitle line, timestamp rendered in yellow for contrast, refresh state uses "Refreshing in Xs" / "Refreshing Xs" language.
- Removed "live" badge from provider panel subtitles — present data implies live; only show "cached Xm" when data is actually stale.
- Removed redundant `<Provider> usage` subtitle text from all provider panels — provider name in the panel title is sufficient.
- Cursor and Vibe panels are now always sorted last in the grid so their compact (3-row) size pairs them together rather than being mixed with taller providers.
- Normalized reset times for Copilot, Cursor, and Vibe to system local time (was UTC). All three now use `target.astimezone().strftime('%b %d at %I:%M %p')` so the display is consistent with Claude, Codex, and Gemini. The `at` notation also lets `_parse_reset_target` parse the string, enabling `_format_reset_display` to compress same-day times to clock-only format.

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
