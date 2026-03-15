"""Provider helper tests."""

from __future__ import annotations

import unittest

from ai_monitor.providers import GeminiProvider


class ProviderHelperTests(unittest.TestCase):
    def test_gemini_extracts_trailing_json_line(self) -> None:
        payload = GeminiProvider._extract_json_line(
            "Loaded cached credentials.\nExperiments loaded {}\n"
            '{"tier":"Gemini Code Assist in Google One AI Pro","buckets":[]}\n'
        )
        self.assertEqual(payload, {"tier": "Gemini Code Assist in Google One AI Pro", "buckets": []})


if __name__ == "__main__":
    unittest.main()
