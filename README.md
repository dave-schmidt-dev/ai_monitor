# ai_monitor

Real-time terminal monitor for local `codex` and `claude` CLI usage.

This project uses the same core shortcut as [`steipete/CodexBar`](https://github.com/steipete/CodexBar): it launches your locally authenticated CLI inside a PTY, sends `/status` or `/usage`, strips terminal control sequences, parses the rendered panel, and refreshes on a timer.

![Warmup screen](docs/screenshots/warmup.png)

![Live dashboard](docs/screenshots/dashboard.png)

## Features

- Monitors Codex usage via `/status`
- Monitors Claude usage via `/usage`
- Reuses persistent PTY sessions to reduce refresh latency after startup
- Refreshes every 60 seconds by default
- Shows 5-hour and 1-week session usage, reset times, and pace indicators
- Renders a compact split dashboard optimized for terminal use
- Exposes `--json` output for scripting and automation
- Includes parser tests for representative Codex and Claude output

## Requirements

- Python 3.10+
- `codex` installed and authenticated on your `PATH`
- `claude` installed and authenticated on your `PATH`
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
python3 -m ai_monitor --json
python3 -m ai_monitor --debug
./monitor --once
```

## How It Works

1. Start a persistent PTY-backed session for each CLI.
2. Send `/status` to Codex and `/usage` to Claude.
3. Capture the rendered terminal output.
4. Strip ANSI/control sequences and normalize the text.
5. Parse usage percentages and reset windows.
6. Re-render the dashboard on the chosen refresh interval.

This is intentionally a CLI/TUI scraping approach, not an official provider API integration.

## Output

Each provider card shows:

- `5h session`: remaining usage for the current 5-hour window
- `5h resets`: next 5-hour reset time
- `5h pace`: whether current usage is ahead of or behind the window pace
- `1w session`: remaining usage for the current 1-week window
- `1w resets`: next weekly reset time
- `1w pace`: weekly pace indicator

## Notes

- `codex` is launched with `-s read-only -a untrusted --no-alt-screen` to keep the probe conservative.
- `claude` is launched in an interactive PTY and the probe auto-accepts the folder trust prompt if it appears.
- The first refresh is slower because the local CLI sessions need to start and render their initial TUI state.
- After startup, the monitor reuses those PTY sessions to make subsequent refreshes faster.

## Limitations

- This depends on the current terminal output format of the `codex` and `claude` CLIs.
- If either vendor changes its TUI wording or layout, the parser may need to be updated.
- Reset windows are only shown when the CLI output exposes them.
- Terminal rendering can vary across fonts and terminal emulators.

## Validation

```bash
python3 -m unittest discover -s tests -v
```
