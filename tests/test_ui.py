"""UI rendering tests for shared provider cards."""

from __future__ import annotations

from datetime import datetime
import json
import os
from unittest.mock import patch
import unittest

from ai_monitor.parsing import strip_ansi
from ai_monitor.providers import ProviderSnapshot
from ai_monitor.ui import PROVIDER_RENDER_SPECS, _build_usage_rows, _format_reset_display, render_json, render_screen


class UIRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updated_at = datetime(2026, 3, 14, 8, 22, 30)
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

    def test_shared_usage_row_builder_keeps_labels_aligned(self) -> None:
        expected_labels = [
            "5h session",
            "5h resets",
            "5h pace",
            "1w session",
            "1w resets",
            "1w pace",
        ]
        for provider_name, data in (("Codex", self.codex_data), ("Claude", self.claude_data)):
            rows = _build_usage_rows(data, 44, self.updated_at, PROVIDER_RENDER_SPECS[provider_name].windows)
            plain_rows = [strip_ansi(row) for row in rows]
            self.assertEqual(len(plain_rows), len(expected_labels))
            for row, label in zip(plain_rows, expected_labels, strict=True):
                self.assertIn(label, row)

    def test_render_screen_reuses_shared_labels_for_both_provider_cards(self) -> None:
        snapshots = [
            ProviderSnapshot(name="Codex", ok=True, source="cli", data=self.codex_data),
            ProviderSnapshot(name="Claude", ok=True, source="cli", data=self.claude_data),
        ]

        screen = strip_ansi(render_screen(snapshots, self.updated_at, 30))

        self.assertIn("OpenAI CLI quota view", screen)
        self.assertIn("Anthropic CLI usage view", screen)
        for label in ("5h session", "5h resets", "5h pace", "1w session", "1w resets", "1w pace"):
            self.assertEqual(screen.count(label), 2)

    def test_format_reset_display_normalizes_24_hour_same_day_times(self) -> None:
        value = _format_reset_display("Resets 13:16", self.updated_at)
        self.assertEqual(value, "1:16 PM")

    def test_format_reset_display_normalizes_relative_times(self) -> None:
        value = _format_reset_display("Resets in 2h 14m", self.updated_at)
        self.assertEqual(value, "10:36 AM")

    def test_format_reset_display_normalizes_date_stamped_provider_formats(self) -> None:
        cases = {
            "Resets on Mar 18, 9:00AM": "Mar 18 9:00 AM",
            "resets 03:09 on 17 Mar": "Mar 17 3:09 AM",
            "Resets Mar 17 at 4 pm": "Mar 17 4:00 PM",
            "Resets 10pm (EDT)": "10:00 PM",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_format_reset_display(raw, self.updated_at), expected)

    def test_render_json_includes_canonical_reset_display_fields(self) -> None:
        snapshots = [
            ProviderSnapshot(name="Codex", ok=True, source="cli", data=self.codex_data),
            ProviderSnapshot(name="Claude", ok=True, source="cli", data=self.claude_data),
        ]

        payload = json.loads(render_json(snapshots, self.updated_at))

        codex = next(provider for provider in payload["providers"] if provider["name"] == "Codex")
        claude = next(provider for provider in payload["providers"] if provider["name"] == "Claude")

        self.assertEqual(codex["display"]["five_hour_reset_display"], "1:16 PM")
        self.assertEqual(codex["display"]["weekly_reset_display"], "Mar 17 9:00 PM")
        self.assertEqual(claude["display"]["five_hour_reset_display"], "1:16 PM")
        self.assertEqual(claude["display"]["weekly_reset_display"], "Mar 17 8:00 PM")

    def test_render_screen_uses_two_column_grid_for_three_cards(self) -> None:
        snapshots = [
            ProviderSnapshot(name="Codex", ok=True, source="cli", data=self.codex_data),
            ProviderSnapshot(name="Claude", ok=True, source="cli", data=self.claude_data),
            ProviderSnapshot(name="Gemini", ok=True, source="cli", data=self.gemini_data),
        ]

        with patch("ai_monitor.ui.shutil.get_terminal_size", return_value=os.terminal_size((92, 30))):
            screen = strip_ansi(render_screen(snapshots, self.updated_at, 30))

        self.assertIn("Google CLI usage view", screen)
        self.assertTrue(any("Codex" in line and "Claude" in line for line in screen.splitlines()))
        self.assertIn("Gemini", screen)


if __name__ == "__main__":
    unittest.main()
