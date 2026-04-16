"""UI rendering tests — Rich-based rendering pipeline."""

from __future__ import annotations

from datetime import datetime
from io import StringIO
import json
import unittest

from rich.console import Console

from ai_monitor.providers import ProviderSnapshot
from ai_monitor.ui import (
    THEME,
    PercentageBar,
    _format_reset_display,
    _style_for_percent,
    build_dashboard,
    build_loading_screen,
    build_provider_panel,
    render_json,
)


def _capture(renderable, *, width: int = 80) -> str:
    """Render a Rich renderable to plain text via Console capture."""
    console = Console(
        file=StringIO(),
        theme=THEME,
        force_terminal=True,
        width=width,
        no_color=True,
    )
    console.print(renderable)
    return console.file.getvalue()


class StyleForPercentTests(unittest.TestCase):
    def test_green_threshold(self) -> None:
        self.assertEqual(_style_for_percent(75), "bar.green")
        self.assertEqual(_style_for_percent(70), "bar.green")

    def test_yellow_threshold(self) -> None:
        self.assertEqual(_style_for_percent(45), "bar.yellow")
        self.assertEqual(_style_for_percent(40), "bar.yellow")

    def test_orange_threshold(self) -> None:
        self.assertEqual(_style_for_percent(25), "bar.orange")
        self.assertEqual(_style_for_percent(20), "bar.orange")

    def test_red_threshold(self) -> None:
        self.assertEqual(_style_for_percent(10), "bar.red")
        self.assertEqual(_style_for_percent(0), "bar.red")

    def test_none_returns_muted(self) -> None:
        self.assertEqual(_style_for_percent(None), "text.muted")


class PercentageBarTests(unittest.TestCase):
    def test_filled_bar_contains_block_chars(self) -> None:
        output = _capture(PercentageBar(68.0, "bar.green"), width=40)
        self.assertIn("▓", output)
        self.assertIn("█", output)
        self.assertIn("░", output)

    def test_none_renders_dots(self) -> None:
        output = _capture(PercentageBar(None, "text.muted"), width=30)
        self.assertIn("·", output)
        self.assertNotIn("▓", output)

    def test_zero_renders_all_empty(self) -> None:
        output = _capture(PercentageBar(0.0, "bar.red"), width=20)
        self.assertNotIn("▓", output)
        self.assertIn("░", output)

    def test_hundred_renders_all_filled(self) -> None:
        output = _capture(PercentageBar(100.0, "bar.green"), width=20)
        self.assertNotIn("░", output)
        self.assertIn("█", output)


class ProviderPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 3, 14, 8, 22, 30)
        self.codex_data = {
            "five_hour_percent_left": 68,
            "five_hour_reset": "Resets 1:16 PM (EDT)",
            "weekly_percent_left": 91,
            "weekly_reset": "Resets Mar 17 at 9 PM",
        }
        self.claude_data = {
            "session_percent_left": 73,
            "primary_reset": "Resets 1:16 PM (EDT)",
            "weekly_percent_left": 64,
            "secondary_reset": "Resets Mar 17 at 8 PM",
        }
        self.gemini_data = {
            "flash_percent_left": 98,
            "flash_reset": "resets in 15h 36m",
            "pro_percent_left": 83,
            "pro_reset": "resets in 22h 23m",
        }
        self.copilot_data = {
            "premium_percent_left": 97.6,
            "premium_reset": "Resets Apr 01 12:00 AM",
        }

    def test_codex_panel_contains_labels_and_values(self) -> None:
        snap = ProviderSnapshot(
            name="Codex", ok=True, source="cli", data=self.codex_data
        )
        output = _capture(build_provider_panel(snap, self.now), width=44)
        self.assertIn("Codex", output)
        self.assertIn("Codex usage", output)
        self.assertIn("5h session", output)
        self.assertIn("1w session", output)
        self.assertIn("5h resets", output)
        self.assertIn("1w resets", output)
        self.assertIn("68%", output)
        self.assertIn("91%", output)

    def test_claude_panel_contains_labels(self) -> None:
        snap = ProviderSnapshot(
            name="Claude", ok=True, source="cli", data=self.claude_data
        )
        output = _capture(build_provider_panel(snap, self.now), width=44)
        self.assertIn("Claude", output)
        self.assertIn("Claude usage", output)
        self.assertIn("5h pace", output)
        self.assertIn("1w pace", output)

    def test_gemini_panel_shows_flash_and_pro(self) -> None:
        snap = ProviderSnapshot(
            name="Gemini", ok=True, source="cli", data=self.gemini_data
        )
        output = _capture(build_provider_panel(snap, self.now), width=44)
        self.assertIn("Gemini", output)
        self.assertIn("flash pool", output)
        self.assertIn("pro pool", output)

    def test_copilot_panel_shows_monthly_metrics(self) -> None:
        snap = ProviderSnapshot(
            name="Copilot", ok=True, source="cli", data=self.copilot_data
        )
        output = _capture(build_provider_panel(snap, self.now), width=44)
        self.assertIn("Copilot", output)
        self.assertIn("month rem", output)
        self.assertIn("month pace", output)
        self.assertIn("97.6%", output)

    def test_error_panel_shows_error_message(self) -> None:
        snap = ProviderSnapshot(
            name="Claude", ok=False, source="cli", error="connection timeout"
        )
        output = _capture(build_provider_panel(snap, self.now), width=44)
        self.assertIn("Claude", output)
        self.assertIn("error", output)
        self.assertIn("connection timeout", output)

    def test_cursor_panel_shows_credit_metrics(self) -> None:
        snap = ProviderSnapshot(
            name="Cursor",
            ok=True,
            source="api",
            data={
                "credit_percent_left": 82.5,
                "plan_name": "pro",
                "billing_cycle_end": "Resets May 01 12:00 AM UTC",
            },
        )
        output = _capture(build_provider_panel(snap, self.now), width=44)
        self.assertIn("Cursor", output)
        self.assertIn("credit rem", output)
        self.assertIn("82.5%", output)
        self.assertIn("pro", output)

    def test_vibe_panel_shows_monthly_usage(self) -> None:
        snap = ProviderSnapshot(
            name="Vibe",
            ok=True,
            source="api",
            data={
                "usage_percent": 0.17,
                "reset_at": "Resets May 01 12:00 AM UTC",
                "payg_enabled": False,
            },
        )
        output = _capture(build_provider_panel(snap, self.now), width=44)
        self.assertIn("Vibe", output)
        self.assertIn("month rem", output)
        self.assertIn("99.8%", output)

    def test_cached_badge_shows_in_subtitle(self) -> None:
        snap = ProviderSnapshot(
            name="Codex",
            ok=True,
            source="cli (cached)",
            data=self.codex_data,
            cached_since=datetime(2026, 3, 14, 8, 19, 0),
        )
        output = _capture(build_provider_panel(snap, self.now), width=50)
        self.assertIn("cached", output)


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 3, 14, 8, 22, 30)
        self.codex_snap = ProviderSnapshot(
            name="Codex",
            ok=True,
            source="cli",
            data={
                "five_hour_percent_left": 68,
                "five_hour_reset": "Resets 1:16 PM (EDT)",
                "weekly_percent_left": 91,
                "weekly_reset": "Resets Mar 17 at 9 PM",
            },
        )
        self.claude_snap = ProviderSnapshot(
            name="Claude",
            ok=True,
            source="cli",
            data={
                "session_percent_left": 73,
                "primary_reset": "Resets 1:16 PM (EDT)",
                "weekly_percent_left": 64,
                "secondary_reset": "Resets Mar 17 at 8 PM",
            },
        )

    def test_dashboard_shows_header_and_footer(self) -> None:
        dashboard = build_dashboard([self.codex_snap], self.now, 30)
        output = _capture(dashboard, width=80)
        self.assertIn("AI Usage Monitor", output)
        self.assertIn("refresh 30s", output)
        self.assertIn("[q]", output)

    def test_dashboard_updating_badge(self) -> None:
        dashboard = build_dashboard(
            [self.codex_snap], self.now, 0, updating=True, update_elapsed=1.4
        )
        output = _capture(dashboard, width=80)
        self.assertIn("updating", output)

    def test_two_column_grid_at_wide_width(self) -> None:
        dashboard = build_dashboard([self.codex_snap, self.claude_snap], self.now, 30)
        output = _capture(dashboard, width=92)
        # Both provider names should appear on the same line in a 2-column grid
        self.assertIn("Codex", output)
        self.assertIn("Claude", output)
        lines = output.splitlines()
        self.assertTrue(
            any("Codex" in line and "Claude" in line for line in lines),
            "Expected Codex and Claude on the same line in 2-column grid",
        )

    def test_single_panel_at_narrow_width(self) -> None:
        dashboard = build_dashboard([self.codex_snap], self.now, 30)
        output = _capture(dashboard, width=50)
        self.assertIn("Codex", output)


class LoadingScreenTests(unittest.TestCase):
    def test_loading_screen_content(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        screen = build_loading_screen("Fetching data...", now, 2.3)
        output = _capture(screen, width=80)
        self.assertIn("Warming Up", output)
        self.assertIn("startup 2.3s", output)
        self.assertIn("Fetching data...", output)


class FormatResetDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 3, 14, 8, 22, 30)

    def test_normalizes_24_hour_same_day_times(self) -> None:
        value = _format_reset_display("Resets 13:16", self.now)
        self.assertEqual(value, "1:16 PM")

    def test_normalizes_relative_times(self) -> None:
        value = _format_reset_display("Resets in 2h 14m", self.now)
        self.assertEqual(value, "10:36 AM")

    def test_normalizes_date_stamped_provider_formats(self) -> None:
        cases = {
            "Resets on Mar 18, 9:00AM": "Mar 18 9:00 AM",
            "resets 03:09 on 17 Mar": "Mar 17 3:09 AM",
            "Resets Mar 17 at 4 pm": "Mar 17 4:00 PM",
            "Resets 10pm (EDT)": "10:00 PM",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_format_reset_display(raw, self.now), expected)


class RenderJsonTests(unittest.TestCase):
    def test_includes_canonical_reset_display_fields(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        snapshots = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={
                    "five_hour_percent_left": 68,
                    "five_hour_reset": "Resets 1:16 PM (EDT)",
                    "weekly_percent_left": 91,
                    "weekly_reset": "Resets Mar 17 at 9 PM",
                },
            ),
            ProviderSnapshot(
                name="Claude",
                ok=True,
                source="cli",
                data={
                    "session_percent_left": 73,
                    "primary_reset": "Resets 1:16 PM (EDT)",
                    "weekly_percent_left": 64,
                    "secondary_reset": "Resets Mar 17 at 8 PM",
                },
            ),
        ]

        payload = json.loads(render_json(snapshots, now))

        codex = next(p for p in payload["providers"] if p["name"] == "Codex")
        claude = next(p for p in payload["providers"] if p["name"] == "Claude")

        self.assertEqual(codex["display"]["five_hour_reset_display"], "1:16 PM")
        self.assertEqual(codex["display"]["weekly_reset_display"], "Mar 17 9:00 PM")
        self.assertEqual(claude["display"]["five_hour_reset_display"], "1:16 PM")
        self.assertEqual(claude["display"]["weekly_reset_display"], "Mar 17 8:00 PM")


class SharedLabelAlignmentTests(unittest.TestCase):
    """Verify that all windowed providers use the same label pipeline."""

    def test_codex_and_claude_share_same_labels(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        codex_snap = ProviderSnapshot(
            name="Codex",
            ok=True,
            source="cli",
            data={
                "five_hour_percent_left": 68,
                "five_hour_reset": "Resets 1:16 PM (EDT)",
                "weekly_percent_left": 91,
                "weekly_reset": "Resets Mar 17 at 9 PM",
            },
        )
        claude_snap = ProviderSnapshot(
            name="Claude",
            ok=True,
            source="cli",
            data={
                "session_percent_left": 73,
                "primary_reset": "Resets 1:16 PM (EDT)",
                "weekly_percent_left": 64,
                "secondary_reset": "Resets Mar 17 at 8 PM",
            },
        )

        dashboard = build_dashboard([codex_snap, claude_snap], now, 30)
        output = _capture(dashboard, width=92)

        for label in (
            "5h session",
            "5h resets",
            "5h pace",
            "1w session",
            "1w resets",
            "1w pace",
        ):
            self.assertEqual(
                output.count(label),
                2,
                f"Expected label '{label}' to appear exactly 2 times",
            )


class NoANSIRegressionTests(unittest.TestCase):
    """Verify the rendering pipeline never emits raw ANSI escape codes."""

    def _assert_no_ansi(self, output: str) -> None:
        self.assertNotIn("\033[", output, "Raw ANSI escape found in captured output")

    def test_provider_panel_no_ansi_in_captured_output(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        for name, data in (
            ("Codex", {"five_hour_percent_left": 50, "weekly_percent_left": 80}),
            ("Claude", {"session_percent_left": 30, "weekly_percent_left": 90}),
            ("Gemini", {"flash_percent_left": 75, "pro_percent_left": 60}),
            ("Copilot", {"premium_percent_left": 95.0}),
        ):
            with self.subTest(provider=name):
                snap = ProviderSnapshot(name=name, ok=True, source="cli", data=data)
                output = _capture(build_provider_panel(snap, now), width=44)
                self._assert_no_ansi(output)

    def test_error_panel_no_ansi(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        snap = ProviderSnapshot(
            name="Claude", ok=False, source="cli", error="rate limited"
        )
        output = _capture(build_provider_panel(snap, now), width=44)
        self._assert_no_ansi(output)

    def test_dashboard_no_ansi(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        snaps = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={"five_hour_percent_left": 50, "weekly_percent_left": 80},
            ),
            ProviderSnapshot(name="Claude", ok=False, source="cli", error="timeout"),
        ]
        output = _capture(build_dashboard(snaps, now, 60), width=92)
        self._assert_no_ansi(output)

    def test_loading_screen_no_ansi(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        output = _capture(build_loading_screen("loading...", now, 1.5), width=80)
        self._assert_no_ansi(output)


class NarrowTerminalTests(unittest.TestCase):
    """Verify rendering doesn't crash at narrow widths."""

    def test_panel_at_minimum_width(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        snap = ProviderSnapshot(
            name="Codex",
            ok=True,
            source="cli",
            data={"five_hour_percent_left": 50, "weekly_percent_left": 80},
        )
        output = _capture(build_provider_panel(snap, now), width=30)
        self.assertIn("Codex", output)

    def test_dashboard_single_column_at_narrow_width(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        snaps = [
            ProviderSnapshot(
                name="Codex",
                ok=True,
                source="cli",
                data={"five_hour_percent_left": 50},
            ),
            ProviderSnapshot(
                name="Claude",
                ok=True,
                source="cli",
                data={"session_percent_left": 30},
            ),
        ]
        # At narrow width, should still render without error
        output = _capture(build_dashboard(snaps, now, 30), width=40)
        self.assertIn("Codex", output)
        self.assertIn("Claude", output)


class CountdownDisplayTests(unittest.TestCase):
    """Verify countdown values render correctly in the dashboard header."""

    def test_countdown_shows_padded_seconds(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        snap = ProviderSnapshot(
            name="Codex", ok=True, source="cli", data={"five_hour_percent_left": 50}
        )
        for seconds in (120, 60, 5, 1):
            with self.subTest(seconds=seconds):
                dashboard = build_dashboard([snap], now, seconds)
                output = _capture(dashboard, width=80)
                self.assertIn(f"refresh {seconds:02d}s", output)

    def test_updating_shows_elapsed(self) -> None:
        now = datetime(2026, 3, 14, 8, 22, 30)
        snap = ProviderSnapshot(
            name="Codex", ok=True, source="cli", data={"five_hour_percent_left": 50}
        )
        dashboard = build_dashboard([snap], now, 0, updating=True, update_elapsed=3.7)
        output = _capture(dashboard, width=80)
        self.assertIn("updating 3.7s", output)


if __name__ == "__main__":
    unittest.main()
