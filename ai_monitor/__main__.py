"""CLI entrypoint for the AI usage monitor."""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import termios
import time
import tty
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live

from .providers import (
    ClaudeProvider,
    CodexProvider,
    CopilotProvider,
    CursorProvider,
    GeminiProvider,
    ProviderSnapshot,
    VibeProvider,
    fetch_provider_snapshot,
)
from .ui import (
    THEME,
    build_dashboard,
    build_loading_screen,
    render_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor Codex, Claude, Gemini, and Copilot usage in real time."
    )
    parser.add_argument(
        "--interval", type=int, default=120, help="Refresh interval in seconds."
    )
    parser.add_argument(
        "--once", action="store_true", help="Fetch one snapshot and exit."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print JSON instead of the live dashboard."
    )
    parser.add_argument(
        "--debug", action="store_true", help="Show full exception strings from probes."
    )
    parser.add_argument(
        "--providers",
        type=str,
        default=None,
        help="Comma-separated list of providers to enable (e.g. Claude,Codex,Gemini).",
    )
    return parser.parse_args()


def _load_config() -> dict[str, object]:
    """Load .ai_monitor.json from CWD if it exists."""
    config_path = Path(os.getcwd()) / ".ai_monitor.json"
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def initialize_providers(
    cwd: str, enabled: set[str] | None = None
) -> tuple[list[tuple[str, object]], list[object]]:
    providers: list[tuple[str, object]] = []
    cleanup: list[object] = []

    if enabled is None or "Codex" in enabled:
        try:
            codex = CodexProvider(cwd)
            providers.append(("Codex", codex))
            cleanup.append(codex)
        except Exception as exc:  # noqa: BLE001
            providers.append(("Codex", exc))

    if enabled is None or "Claude" in enabled:
        try:
            claude = ClaudeProvider(cwd)
            providers.append(("Claude", claude))
            cleanup.append(claude)
        except Exception as exc:  # noqa: BLE001
            providers.append(("Claude", exc))

    if enabled is None or "Gemini" in enabled:
        try:
            gemini = GeminiProvider(cwd)
            providers.append(("Gemini", gemini))
            cleanup.append(gemini)
        except Exception as exc:  # noqa: BLE001
            providers.append(("Gemini", exc))

    if enabled is None or "Copilot" in enabled:
        try:
            copilot = CopilotProvider(cwd)
            providers.append(("Copilot", copilot))
            cleanup.append(copilot)
        except Exception as exc:  # noqa: BLE001
            providers.append(("Copilot", exc))

    if enabled is None or "Cursor" in enabled:
        try:
            cursor = CursorProvider()
            providers.append(("Cursor", cursor))
            cleanup.append(cursor)
        except Exception as exc:  # noqa: BLE001
            providers.append(("Cursor", exc))

    if enabled is None or "Vibe" in enabled:
        try:
            vibe = VibeProvider(cwd)
            providers.append(("Vibe", vibe))
            cleanup.append(vibe)
        except Exception as exc:  # noqa: BLE001
            providers.append(("Vibe", exc))

    return providers, cleanup


def collect_snapshots(
    providers: list[tuple[str, object]], debug: bool
) -> list[ProviderSnapshot]:
    snapshots: list[ProviderSnapshot] = []
    workers: list[tuple[str, object]] = [
        (name, provider) for name, provider in providers if hasattr(provider, "fetch")
    ]
    static_errors = [
        (name, provider)
        for name, provider in providers
        if not hasattr(provider, "fetch")
    ]

    executor = ThreadPoolExecutor(max_workers=max(1, len(workers)))
    try:
        future_map = {
            executor.submit(fetch_provider_snapshot, name, provider, debug): name
            for name, provider in workers
        }
        for future in future_map:
            snapshots.append(future.result())
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    for name, error in static_errors:
        snapshots.append(
            ProviderSnapshot(name=name, ok=False, source="cli", error=str(error))
        )

    snapshots.sort(key=lambda item: item.name)
    return snapshots


def _is_transient_probe_error(snapshot: ProviderSnapshot) -> bool:
    if snapshot.ok or not snapshot.error:
        return False
    message = snapshot.error.lower()
    transient_markers = (
        "rate limited",
        "failed to load usage data",
        "could not load usage data",
        "empty claude output",
        "empty gemini output",
        "empty copilot output",
        "missing current session",
        "data not available yet",
    )
    return any(marker in message for marker in transient_markers)


def _merge_with_previous(
    previous: list[ProviderSnapshot],
    fresh: list[ProviderSnapshot],
) -> list[ProviderSnapshot]:
    previous_by_name = {snap.name: snap for snap in previous}
    merged: list[ProviderSnapshot] = []
    for snapshot in fresh:
        prior = previous_by_name.get(snapshot.name)
        if prior and prior.ok and _is_transient_probe_error(snapshot):
            merged.append(
                replace(
                    prior,
                    source=f"{prior.source} (cached)",
                    cached_since=prior.cached_since or datetime.now(),
                )
            )
            continue
        if snapshot.ok and snapshot.cached_since is not None:
            merged.append(replace(snapshot, cached_since=None))
            continue
        merged.append(snapshot)
    merged.sort(key=lambda item: item.name)
    return merged


def _extract_percent_left(snap: ProviderSnapshot) -> float | None:
    """Extract the primary percent-left value from any provider snapshot."""
    if not snap.data:
        return None
    for key in (
        "credit_percent_left",
        "premium_percent_left",
        "session_percent_left",
        "five_hour_percent_left",
        "flash_percent_left",
    ):
        value = snap.data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    usage = snap.data.get("usage_percent")
    if isinstance(usage, (int, float)):
        return max(0.0, 100.0 - float(usage))
    return None


def _notify_threshold(
    provider_name: str, percent_left: float, threshold: float
) -> None:
    """Send a macOS notification when a provider is below threshold."""
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{provider_name} at {percent_left:.0f}% remaining" '
                f'with title "AI Monitor" subtitle "Below {threshold:.0f}% threshold"',
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _check_thresholds(
    snapshots: list[ProviderSnapshot],
    threshold: float,
    notified_providers: set[str],
) -> None:
    for snap in snapshots:
        if not snap.ok or not snap.data:
            notified_providers.discard(snap.name)
            continue
        pct = _extract_percent_left(snap)
        if pct is not None and pct < threshold:
            if snap.name not in notified_providers:
                _notify_threshold(snap.name, pct, threshold)
                notified_providers.add(snap.name)
        else:
            notified_providers.discard(snap.name)


@contextmanager
def _cbreak_mode():
    """Put stdin in cbreak mode for single-keypress reading. No-op if not a TTY."""
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main() -> int:
    args = parse_args()
    config = _load_config()
    enabled_providers: set[str] | None = None
    provider_override = getattr(args, "providers", None)
    if provider_override:
        enabled_providers = {
            name.strip() for name in provider_override.split(",") if name.strip()
        }
    elif isinstance(config.get("providers"), list):
        configured = {
            str(name).strip()
            for name in config.get("providers", [])
            if str(name).strip()
        }
        enabled_providers = configured or None
    if config.get("interval") and not any(
        arg.startswith("--interval") for arg in sys.argv[1:]
    ):
        try:
            args.interval = int(config["interval"])
        except (TypeError, ValueError):
            pass
    try:
        threshold = float(config.get("threshold", 20))
    except (TypeError, ValueError):
        threshold = 20.0
    cwd = os.getcwd()
    providers, cleanup = initialize_providers(cwd, enabled_providers)
    notified_providers: set[str] = set()

    def refresh(previous: list[ProviderSnapshot]) -> list[ProviderSnapshot]:
        fresh: list[ProviderSnapshot] = []
        executor = ThreadPoolExecutor(max_workers=len(cleanup) or 1)
        try:
            future_map = {
                executor.submit(
                    fetch_provider_snapshot,
                    provider.__class__.__name__.replace("Provider", ""),
                    provider,
                    args.debug,
                ): provider
                for provider in cleanup
            }
            for future in future_map:
                fresh.append(future.result())
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        static_names = {snap.name for snap in fresh}
        for snap in previous:
            if snap.name not in static_names and not snap.ok:
                fresh.append(snap)
        return _merge_with_previous(previous, fresh)

    console = Console(theme=THEME)

    try:
        if args.json:
            updated_at = datetime.now()
            snapshots = collect_snapshots(providers, args.debug)
            _check_thresholds(snapshots, threshold, notified_providers)
            sys.stdout.write(render_json(snapshots, updated_at) + "\n")
            sys.stdout.flush()
            return 0

        # --once: block on initial fetch, print dashboard, exit (no alt-screen)
        if args.once:
            snapshots = collect_snapshots(providers, args.debug)
            _check_thresholds(snapshots, threshold, notified_providers)
            updated_at = datetime.now()
            console.print(
                build_dashboard(snapshots, updated_at, 0, threshold=threshold)
            )
            return 0

        # Live interactive mode
        with _cbreak_mode():
            with Live(
                console=console,
                screen=True,
                auto_refresh=False,
            ) as live:
                # Loading phase
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(collect_snapshots, providers, args.debug)
                    started = time.monotonic()
                    while not future.done():
                        live.update(
                            build_loading_screen(
                                "Getting initial usage from Claude, Codex, Copilot, Cursor, Gemini, and Vibe…",
                                datetime.now(),
                                time.monotonic() - started,
                            )
                        )
                        live.refresh()
                        time.sleep(0.12)
                    current = future.result()
                    _check_thresholds(current, threshold, notified_providers)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)

                # Main refresh loop
                quit_requested = False
                while not quit_requested:
                    updated_at = datetime.now()

                    # Countdown phase with deadline-based drift correction
                    deadline = time.monotonic() + args.interval
                    remaining = args.interval
                    refresh_now = False
                    while remaining > 0 and not quit_requested:
                        live.update(
                            build_dashboard(
                                current,
                                updated_at,
                                remaining,
                                threshold=threshold,
                            )
                        )
                        live.refresh()
                        sleep_until = deadline - remaining + 1
                        wait_time = max(0.0, sleep_until - time.monotonic())
                        if sys.stdin.isatty():
                            readable, _, _ = select.select(
                                [sys.stdin], [], [], wait_time
                            )
                            if readable:
                                key = sys.stdin.read(1)
                                if key in ("q", "Q"):
                                    quit_requested = True
                                    break
                                if key in ("r", "R"):
                                    refresh_now = True
                                    break
                        else:
                            time.sleep(wait_time)
                        remaining -= 1

                    if quit_requested:
                        break
                    if not refresh_now and remaining > 0:
                        continue

                    # Refresh phase: show updating spinner while fetching
                    refresh_executor = ThreadPoolExecutor(max_workers=1)
                    try:
                        refresh_started = time.monotonic()
                        refresh_future = refresh_executor.submit(refresh, current)
                        while not refresh_future.done():
                            live.update(
                                build_dashboard(
                                    current,
                                    datetime.now(),
                                    0,
                                    updating=True,
                                    update_elapsed=time.monotonic() - refresh_started,
                                    threshold=threshold,
                                )
                            )
                            live.refresh()
                            if sys.stdin.isatty():
                                readable, _, _ = select.select(
                                    [sys.stdin], [], [], 0.12
                                )
                                if readable:
                                    key = sys.stdin.read(1)
                                    if key in ("q", "Q"):
                                        quit_requested = True
                                        break
                            else:
                                time.sleep(0.12)
                        if not quit_requested:
                            current = refresh_future.result()
                            _check_thresholds(current, threshold, notified_providers)
                    finally:
                        refresh_executor.shutdown(wait=False, cancel_futures=True)

                    if quit_requested:
                        break

    except KeyboardInterrupt:
        return 0
    finally:
        for provider in cleanup:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
