"""Provider helper tests."""

from __future__ import annotations

import unittest

from ai_monitor.providers import CopilotProvider, GeminiProvider, _is_terminal_probe_noise


class ProviderHelperTests(unittest.TestCase):
    def test_gemini_extracts_trailing_json_line(self) -> None:
        payload = GeminiProvider._extract_json_line(
            "Loaded cached credentials.\nExperiments loaded {}\n"
            '{"tier":"Gemini Code Assist in Google One AI Pro","buckets":[]}\n'
        )
        self.assertEqual(payload, {"tier": "Gemini Code Assist in Google One AI Pro", "buckets": []})

    def test_gemini_detects_waiting_for_authentication_banner(self) -> None:
        self.assertTrue(
            GeminiProvider._is_waiting_for_authentication(
                "╭────────────────╮\n│ Waiting for authentication... │\n╰────────────────╯"
            )
        )

    def test_gemini_ignores_non_authentication_output(self) -> None:
        self.assertFalse(GeminiProvider._is_waiting_for_authentication("Session Stats\nUsage remaining"))

    def test_detects_terminal_probe_noise(self) -> None:
        self.assertTrue(_is_terminal_probe_noise("10;?"))
        self.assertTrue(_is_terminal_probe_noise("11;?0;"))
        self.assertFalse(_is_terminal_probe_noise("5h limit: 70% left"))

    def test_copilot_monthly_reset_label_uses_utc(self) -> None:
        label = CopilotProvider._monthly_reset_label()
        self.assertTrue(label.startswith("Resets "))
        self.assertTrue(label.endswith(" UTC"))


if __name__ == "__main__":
    unittest.main()
