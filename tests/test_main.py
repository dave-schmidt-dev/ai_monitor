"""Entrypoint regression tests."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch

from ai_monitor.__main__ import main
from ai_monitor.providers import ProviderSnapshot


class _FakeFuture:
    def __init__(self, value: list[ProviderSnapshot]) -> None:
        self._value = value
        self.done_calls = 0

    def done(self) -> bool:
        self.done_calls += 1
        return self.done_calls > 3

    def result(self) -> list[ProviderSnapshot]:
        return self._value


class _FakeExecutor:
    def __init__(self, future: _FakeFuture) -> None:
        self._future = future

    def submit(self, *_args, **_kwargs) -> _FakeFuture:
        return self._future

    def shutdown(self, **_kwargs) -> None:  # noqa: D401
        return None


class MainRegressionTests(unittest.TestCase):
    def test_startup_loading_animates_before_once_output(self) -> None:
        snapshots = [ProviderSnapshot(name="Codex", ok=True, source="cli", data={"five_hour_percent_left": 75})]
        fake_future = _FakeFuture(snapshots)
        fake_executor = _FakeExecutor(fake_future)

        with (
            patch("ai_monitor.__main__.parse_args", return_value=argparse.Namespace(json=False, once=True, debug=False, interval=120)),
            patch("ai_monitor.__main__.initialize_providers", return_value=([], [])),
            patch("ai_monitor.__main__.ThreadPoolExecutor", return_value=fake_executor),
            patch("ai_monitor.__main__.render_loading_screen", return_value="LOADING"),
            patch("ai_monitor.__main__.render_screen", return_value="FINAL"),
            patch("ai_monitor.__main__.write_screen") as write_screen,
        ):
            rc = main()

        self.assertEqual(rc, 0)
        self.assertEqual(fake_future.done_calls, 4)
        self.assertEqual(write_screen.call_count, 5)
        self.assertEqual(write_screen.call_args_list[0].args[0], "LOADING")
        self.assertEqual(write_screen.call_args_list[-2].args[0], "FINAL")
        self.assertEqual(write_screen.call_args_list[-2].kwargs.get("repaint"), False)
        self.assertEqual(write_screen.call_args_list[-1].args[0], "\n")

    def test_live_mode_enters_and_exits_alt_screen_session(self) -> None:
        snapshots = [ProviderSnapshot(name="Codex", ok=True, source="cli", data={"five_hour_percent_left": 75})]
        fake_future = _FakeFuture(snapshots)
        fake_executor = _FakeExecutor(fake_future)

        with (
            patch("ai_monitor.__main__.parse_args", return_value=argparse.Namespace(json=False, once=False, debug=False, interval=120)),
            patch("ai_monitor.__main__.initialize_providers", return_value=([], [])),
            patch("ai_monitor.__main__.ThreadPoolExecutor", return_value=fake_executor),
            patch("ai_monitor.__main__.countdown_sleep", side_effect=KeyboardInterrupt),
            patch("ai_monitor.__main__.render_loading_screen", return_value="LOADING"),
            patch("ai_monitor.__main__.render_screen", return_value="FINAL"),
            patch("ai_monitor.__main__.write_screen"),
            patch("ai_monitor.__main__.start_live_ui") as start_live_ui,
            patch("ai_monitor.__main__.end_live_ui") as end_live_ui,
        ):
            rc = main()

        self.assertEqual(rc, 0)
        start_live_ui.assert_called_once()
        end_live_ui.assert_called_once()


if __name__ == "__main__":
    unittest.main()
