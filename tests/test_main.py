"""Entrypoint regression tests."""

from __future__ import annotations

import argparse
import json
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from rich.console import Console

from ai_monitor.__main__ import main
from ai_monitor.providers import ProviderSnapshot
from ai_monitor.ui import THEME, build_dashboard


class MainOnceTests(unittest.TestCase):
    """Test --once mode: no Live context, prints dashboard via Console.print."""

    def test_once_prints_dashboard_without_live(self) -> None:
        snapshots = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={"five_hour_percent_left": 75},
            )
        ]

        with (
            patch(
                "ai_monitor.__main__.parse_args",
                return_value=argparse.Namespace(
                    json=False, once=True, debug=False, interval=120
                ),
            ),
            patch("ai_monitor.__main__.initialize_providers", return_value=([], [])),
            patch("ai_monitor.__main__.collect_snapshots", return_value=snapshots),
            patch("ai_monitor.__main__.Console") as MockConsole,
        ):
            mock_console = MagicMock()
            MockConsole.return_value = mock_console
            rc = main()

        self.assertEqual(rc, 0)
        mock_console.print.assert_called_once()

    def test_once_does_not_use_live_context(self) -> None:
        """--once must never enter alt-screen (no Live)."""
        snapshots = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={"five_hour_percent_left": 75},
            )
        ]

        with (
            patch(
                "ai_monitor.__main__.parse_args",
                return_value=argparse.Namespace(
                    json=False, once=True, debug=False, interval=120
                ),
            ),
            patch("ai_monitor.__main__.initialize_providers", return_value=([], [])),
            patch("ai_monitor.__main__.collect_snapshots", return_value=snapshots),
            patch("ai_monitor.__main__.Console") as MockConsole,
            patch("ai_monitor.__main__.Live") as MockLive,
        ):
            MockConsole.return_value = MagicMock()
            main()

        MockLive.assert_not_called()


class MainJsonTests(unittest.TestCase):
    """Test --json mode: writes JSON to stdout, no Console or Live."""

    def test_json_writes_valid_json_to_stdout(self) -> None:
        snapshots = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={
                    "five_hour_percent_left": 75,
                    "five_hour_reset": "Resets 1:16 PM",
                    "weekly_percent_left": 91,
                    "weekly_reset": "Resets Mar 17 at 9 PM",
                },
            ),
            ProviderSnapshot(
                name="Claude",
                ok=False,
                source="cli",
                error="connection timeout",
            ),
        ]

        buf = StringIO()
        with (
            patch(
                "ai_monitor.__main__.parse_args",
                return_value=argparse.Namespace(
                    json=True, once=False, debug=False, interval=120
                ),
            ),
            patch("ai_monitor.__main__.initialize_providers", return_value=([], [])),
            patch("ai_monitor.__main__.collect_snapshots", return_value=snapshots),
            patch("ai_monitor.__main__.sys.stdout", buf),
        ):
            rc = main()

        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("updated_at", payload)
        self.assertIn("providers", payload)
        self.assertEqual(len(payload["providers"]), 2)

        codex = next(p for p in payload["providers"] if p["name"] == "Codex")
        self.assertTrue(codex["ok"])
        self.assertIn("display", codex)
        self.assertIn("five_hour_reset_display", codex["display"])

        claude = next(p for p in payload["providers"] if p["name"] == "Claude")
        self.assertFalse(claude["ok"])
        self.assertEqual(claude["error"], "connection timeout")

    def test_json_does_not_contain_ansi_escapes(self) -> None:
        """JSON output must never contain ANSI escape sequences."""
        snapshots = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={"five_hour_percent_left": 75},
            )
        ]

        buf = StringIO()
        with (
            patch(
                "ai_monitor.__main__.parse_args",
                return_value=argparse.Namespace(
                    json=True, once=False, debug=False, interval=120
                ),
            ),
            patch("ai_monitor.__main__.initialize_providers", return_value=([], [])),
            patch("ai_monitor.__main__.collect_snapshots", return_value=snapshots),
            patch("ai_monitor.__main__.sys.stdout", buf),
        ):
            main()

        self.assertNotIn("\033[", buf.getvalue())


class DashboardNoANSILeakageTests(unittest.TestCase):
    """Verify Rich rendering output contains no raw ANSI when captured with no_color."""

    def test_dashboard_no_color_output_has_no_escapes(self) -> None:
        """When captured via no_color=True, output must be pure text."""
        snapshots = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={
                    "five_hour_percent_left": 68,
                    "five_hour_reset": "Resets 1:16 PM",
                    "weekly_percent_left": 91,
                    "weekly_reset": "Resets Mar 17 at 9 PM",
                },
            ),
            ProviderSnapshot(
                name="Claude",
                ok=False,
                source="cli",
                error="rate limited",
            ),
        ]
        from datetime import datetime

        now = datetime(2026, 3, 14, 8, 22, 30)
        dashboard = build_dashboard(snapshots, now, 30)
        console = Console(
            file=StringIO(),
            theme=THEME,
            force_terminal=True,
            width=92,
            no_color=True,
        )
        console.print(dashboard)
        output = console.file.getvalue()

        self.assertNotIn("\033[", output)
        # Should still have meaningful content
        self.assertIn("Codex", output)
        self.assertIn("Claude", output)
        self.assertIn("68%", output)
        self.assertIn("rate limited", output)


if __name__ == "__main__":
    unittest.main()
