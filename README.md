# ai_monitor

Real-time terminal monitor for local `codex`, `claude`, `gemini`, `copilot`, `cursor`, and `vibe` usage.

This project uses the same core shortcut as [`steipete/CodexBar`](https://github.com/steipete/CodexBar): it launches your locally authenticated CLI inside a PTY, sends `/status` or `/usage`, strips terminal control sequences, parses the rendered panel, and refreshes on a timer. Gemini now also has a direct internal quota probe fallback because its `/stats` TUI was not stable enough to scrape reliably.

![Warmup screen](docs/screenshots/warmup.png)

![Live dashboard](docs/screenshots/dashboard.png)

## Features

- Monitors Codex usage via `/status`
- Monitors Claude usage via `/usage`
- Monitors Gemini usage via `/stats`
- Monitors Copilot premium-request status from the interactive CLI status line
- Monitors Cursor credit usage via the Cursor Dashboard API
- Monitors Vibe usage via the Mistral billing API
- Handles Codex transient PTY probe noise and retries until a real status panel is captured
- Reuses persistent PTY sessions to reduce refresh latency after startup
- Refreshes every 120 seconds by default
- Shows Codex and Claude 5-hour and 1-week session usage, reset times, and pace indicators
- Shows Gemini Flash and Pro pool remaining percentages with reset countdowns
- Shows Copilot monthly premium remaining (`month rem`) with a color progress bar, monthly reset (`month reset`), and monthly pace (`month pace`) in the same card style
- Shows Cursor credit remaining (`credit rem`), reset, plan, and billing-cycle pace
- Shows Vibe monthly remaining (`month rem`), reset, and billing-cycle pace
- Shows compact single-line error cards to reduce vertical noise when a provider is unavailable
- Supports live keyboard shortcuts (`q` quit, `r` refresh now)
- Supports `.ai_monitor.json` for provider selection, interval, and threshold configuration
- Sends one-shot macOS threshold notifications and marks low providers with a `[!]` badge
- Uses a shared provider card renderer so reset labels and pacing rows stay aligned across providers
- Canonicalizes reset displays to one local format across provider-specific strings
- Renders a compact grid dashboard optimized for terminal use
- Exposes `--json` output for scripting and automation, including normalized reset display fields
- Includes parser tests for representative Codex and Claude output

## Requirements

- Python 3.10+
- `codex` installed and authenticated on your `PATH`
- `claude` installed and authenticated on your `PATH`
- `gemini` installed and authenticated on your `PATH`
- `copilot` installed and authenticated on your `PATH`
- Cursor app or browser session authenticated
- Mistral console session authenticated (Safari/Chrome cookie extraction supported)
- `rich>=15.0` (installed automatically via `pip install` or `uv sync`)
- A terminal that supports ANSI color

## Run

```bash
python3 -m ai_monitor
./monitor
```

Useful options:

```bash
python3 -m ai_monitor --once
python3 -m ai_monitor --interval 30
python3 -m ai_monitor --interval 60
python3 -m ai_monitor --json
python3 -m ai_monitor --debug
python3 -m ai_monitor --providers Claude,Codex,Gemini
./monitor --once
```

When `--debug` is enabled, raw captures are written to `/tmp/ai_monitor_*_capture.txt`.

Optional config file (`.ai_monitor.json` in your current working directory):

```json
{
  "providers": ["Claude", "Codex", "Copilot", "Cursor", "Gemini", "Vibe"],
  "interval": 120,
  "threshold": 20
}
```

## How It Works

1. Start a persistent PTY-backed session for CLI-backed providers.
2. Send `/status` to Codex and `/usage` to Claude.
3. Probe Gemini through the installed CLI's internal quota/config path, with PTY `/stats` as a fallback.
4. Probe Copilot through an interactive PTY warmup and parse remaining premium percentage from the status line.
5. Capture the rendered terminal output or structured quota payload.
6. Strip ANSI/control sequences and normalize the text.
7. Parse usage percentages and reset windows.
8. Re-render the dashboard on the chosen refresh interval and watch for threshold crossings.

This is intentionally a CLI/TUI scraping approach, not an official provider API integration.

## Output

Codex and Claude cards show:

- `5h session`: remaining usage for the current 5-hour window
- `5h resets`: next 5-hour reset time
- `5h pace`: whether current usage is ahead of or behind the window pace
- `1w session`: remaining usage for the current 1-week window
- `1w resets`: next weekly reset time
- `1w pace`: weekly pace indicator

Gemini cards show:

- `flash pool`: flash pool remaining usage
- `flash reset`: flash pool reset countdown
- `pro pool`: pro pool remaining usage
- `pro reset`: pro pool reset countdown
- `pace n/a`: shown intentionally because Gemini does not expose a full window start/end for a true pace calculation

Copilot card shows:

- `month rem`: remaining premium percentage, rendered with one decimal place
- `month reset`: monthly reset target (first day of next month at 12:00 AM UTC)
- `month pace`: pace vs expected month progress (`under pace`, `on pace`, or `over pace`)

Cursor card shows:

- `credit rem`: remaining usage credits
- `resets`: billing cycle reset display string
- `credit pace`: pace across the current billing cycle
- `plan`: plan name (when available)

Vibe card shows:

- `month rem`: remaining monthly allowance (`100 - usage_percent`)
- `month reset`: reset time from the API
- `month pace`: pace across the current billing cycle

Reset displays are normalized before rendering:

- Same-day resets render as `h:mm AM/PM`
- Future resets render as `Mon DD h:mm AM/PM`
- Relative vendor text like `Resets in 2h 14m` is converted into the same absolute local display

## JSON Output

`--json` preserves the raw provider payload under `data` and adds normalized reset display fields under `display`.

Example:

```json
{
  "updated_at": "2026-03-14T08:22:30",
  "providers": [
    {
      "name": "Codex",
      "ok": true,
      "source": "cli",
      "data": {
        "five_hour_reset": "Resets 13:16",
        "weekly_reset": "Resets on Mar 18, 9:00AM"
      },
      "display": {
        "five_hour_reset_display": "1:16 PM",
        "weekly_reset_display": "Mar 18 9:00 AM"
      },
      "error": null
    }
  ]
}
```

## Notes

- `codex` is launched with `-s read-only -a untrusted --no-alt-screen` to keep the probe conservative.
- `claude` is launched in an interactive PTY and the probe auto-accepts the folder trust prompt if it appears.
- `gemini` prefers a direct internal quota probe against the installed Gemini CLI and only falls back to PTY `/stats` scraping if that direct path fails.
- `copilot` probing parses passive status-line metadata only (no prompt submission or usage endpoint calls).
- Gemini internal probing supports both legacy `dist/src/config/*.js` layouts and modern bundled Homebrew layouts (`bundle/chunk-*.js`).
- Claude `/usage` parsing tolerates compressed single-line usage panels where session/week rows are rendered without line breaks.
- The first refresh is slower because the local CLI sessions need to start and render their initial TUI state.
- After startup, the monitor reuses those PTY sessions to make subsequent refreshes faster.
- During each timed refresh, the header switches from `refresh XXs` to a single in-place `updating …` state until all providers complete, then resumes the countdown.
- Live rendering uses the `rich` library's `Live` display with alt-screen mode, eliminating scrollback buffer growth.
- In live mode, press `q` to quit or `r` to trigger an immediate refresh.
- Cursor and Vibe try Safari cookie extraction first; Vibe also supports Chrome cookie extraction.
- Providers below threshold show a `[!]` badge and trigger one-shot macOS notifications until they recover above threshold.

## Limitations

- This depends on current local CLI/API behavior across `codex`, `claude`, `gemini`, `copilot`, `cursor`, and `vibe`.
- If any vendor changes its TUI wording or layout, the parser may need to be updated.
- Reset windows are only shown when the CLI output exposes them.
- Terminal rendering can vary across fonts and terminal emulators.
- Copilot currently relies on the status-line remaining percentage signal; if Copilot CLI omits it, `month rem` and `month pace` can still show `n/a`.

## Known Issues

- **Claude `/usage` may return "only available for subscription plans"** even on valid Team or Pro seats. This is a server-side issue where the Anthropic usage API returns empty limit buckets (`five_hour`, `seven_day`, `seven_day_sonnet` are all null). The PTY probe itself works correctly. When the API starts returning data again, the Claude card will populate automatically.
- Gemini prefers a direct internal quota probe and only falls back to PTY `/stats` scraping if that path is unavailable.
- If Gemini falls back to PTY probing and shows a **waiting for authentication** screen, `ai_monitor` now reports that directly. Run `gemini` once and complete sign-in, then rerun the monitor.

## Validation

```bash
python3 -m unittest discover -s tests -v
```
