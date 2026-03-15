"""CLI entrypoint for the AI usage monitor."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import os
import sys
import time

from .providers import ClaudeProvider, CodexProvider, GeminiProvider, ProviderSnapshot, fetch_provider_snapshot
from .ui import countdown_sleep, render_json, render_loading_screen, render_screen, write_screen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Codex and Claude usage in real time.")
    parser.add_argument("--interval", type=int, default=120, help="Refresh interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Fetch one snapshot and exit.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the live dashboard.")
    parser.add_argument("--debug", action="store_true", help="Show full exception strings from probes.")
    return parser.parse_args()


def initialize_providers(cwd: str) -> tuple[list[tuple[str, object]], list[object]]:
    providers: list[tuple[str, object]] = []
    cleanup: list[object] = []

    try:
        codex = CodexProvider(cwd)
        providers.append(("Codex", codex))
        cleanup.append(codex)
    except Exception as exc:  # noqa: BLE001
        providers.append(("Codex", exc))

    try:
        claude = ClaudeProvider(cwd)
        providers.append(("Claude", claude))
        cleanup.append(claude)
    except Exception as exc:  # noqa: BLE001
        providers.append(("Claude", exc))

    try:
        gemini = GeminiProvider(cwd)
        providers.append(("Gemini", gemini))
        cleanup.append(gemini)
    except Exception as exc:  # noqa: BLE001
        providers.append(("Gemini", exc))

    return providers, cleanup


def collect_snapshots(providers: list[tuple[str, object]], debug: bool) -> list[ProviderSnapshot]:
    snapshots: list[ProviderSnapshot] = []
    workers: list[tuple[str, object]] = [(name, provider) for name, provider in providers if hasattr(provider, "fetch")]
    static_errors = [(name, provider) for name, provider in providers if not hasattr(provider, "fetch")]

    executor = ThreadPoolExecutor(max_workers=max(1, len(workers)))
    try:
        future_map = {
            executor.submit(fetch_provider_snapshot, name, provider, debug): name for name, provider in workers
        }
        for future in future_map:
            snapshots.append(future.result())
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    for name, error in static_errors:
        snapshots.append(ProviderSnapshot(name=name, ok=False, source="cli", error=str(error)))

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


def main() -> int:
    args = parse_args()
    cwd = os.getcwd()
    providers, cleanup = initialize_providers(cwd)

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
        for snap in snapshots:
            if snap.name not in static_names and not snap.ok:
                fresh.append(snap)
        return _merge_with_previous(previous, fresh)

    try:
        if args.json:
            updated_at = datetime.now()
            snapshots = collect_snapshots(providers, args.debug)
            write_screen(render_json(snapshots, updated_at) + "\n")
            return 0

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(collect_snapshots, providers, args.debug)
            frame = 0
            started = time.monotonic()
            while not future.done():
                write_screen(
                    render_loading_screen(
                        "Getting initial usage from Codex, Claude, and Gemini…",
                        datetime.now(),
                        frame,
                        time.monotonic() - started,
                    ),
                )
                time.sleep(0.12)
                frame += 1
            snapshots = future.result()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        updated_at = datetime.now()

        if args.once:
            write_screen(render_screen(snapshots, updated_at, 0) + "\n")
            return 0

        current = snapshots

        while True:
            updated_at = datetime.now()
            write_screen(render_screen(current, updated_at, args.interval))

            def render_frame(remaining: int) -> None:
                write_screen(render_screen(current, updated_at, remaining))

            countdown_sleep(args.interval, render_frame)
            current = refresh(current)
    except KeyboardInterrupt:
        write_screen("\n")
        return 0
    finally:
        for provider in cleanup:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
