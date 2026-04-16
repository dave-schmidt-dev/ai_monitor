"""Parser tests for representative Codex, Claude, and Gemini output."""

from __future__ import annotations

import unittest

from ai_monitor.parsing import (
    parse_claude_status,
    parse_codex_status,
    parse_copilot_status,
    parse_gemini_status,
)

CODEX_SAMPLE = """
Credits: 12.50
5h limit: 68% left  Resets in 2h 14m
Weekly limit: 91% left  Resets on Mar 18, 9:00AM
"""


CLAUDE_USAGE_SAMPLE = """
Settings: Account Usage
Current session
27% used
Resets in 3h 02m

Current week (all models)
64% left
Resets on Mar 17, 8:00AM

Current week (Opus)
18% used
Resets on Mar 17, 8:00AM

Account: dave@example.com
Organization: Zero Delta LLC
"""


CLAUDE_STATUS_SAMPLE = """
Login Method: Claude Max 20x
"""


CODEX_LIVE_STYLE_SAMPLE = """
│  5h limit:             [███████████████████░] 96% left   │
│                        (resets 00:15 on 14 Mar)          │
│  Weekly limit:         [██████████████████░░] 92% left   │
│                        (resets 03:09 on 17 Mar)          │
"""


CLAUDE_LIVE_STYLE_SAMPLE = """
Settings: StatusConfig Usage (←/→ or tab to cycle)
Current session · Resets 10pm (America/New_York)████████████████
70%used

Currentweek(allmodels)·ResetsMar17at4pm
(America/New_York)
████████████████
48%used
"""


CLAUDE_COMPACT_SINGLE_LINE_SAMPLE = """
❯ /usage ───────────────────── Status   Config   Usage Stats
Current session · Resets 5pm (America/New_York)██████████████████ 31%usedCurrent week (all models)· Resets 10am (America/New_York)███████████████████████████████████████████████████████▊ 96%usedCurrent week (Sonnet only)· Resets Apr 19 at 6pm (America/New_York)███████████▋ 20%used$200 in extra usage for third-party apps · /extra-usageEsc to cancel
"""


GEMINI_STATS_SAMPLE = """
/stats
╭───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│  Session Stats                                                                                                                                │
│  Interaction Summary                                                                                                                          │
│  Session ID:                 0c141004-81ef-41e0-830e-f445518acc49                                                                             │
│  Auth Method:                Logged in with Google (user@example.com)                                                               │
│  Tier:                       Gemini Code Assist in Google One AI Pro                                                                          │
│  Auto (Gemini 3) Usage                                                                                                                        │
│  Model                       Reqs             Usage remaining                                                                                 │
│  gemini-2.5-flash               -     98.3% resets in 15h 36m                                                                                 │
│  gemini-2.5-flash-lite          -     97.4% resets in 15h 36m                                                                                 │
│  gemini-2.5-pro                 -     83.3% resets in 22h 23m                                                                                 │
│  gemini-3-flash-preview         -     98.3% resets in 15h 36m                                                                                 │
│  gemini-3.1-pro-preview         -     83.3% resets in 22h 23m                                                                                 │
"""


COPILOT_STATUS_SAMPLE = """
● Changes   +12 -3  Requests  7 Premium (2m)
"""

COPILOT_REMAINING_SAMPLE = """
 / commands · ? help                 Remaining reqs.: 97.6%
"""


class ParsingTests(unittest.TestCase):
    def test_parse_codex_status(self) -> None:
        status = parse_codex_status(CODEX_SAMPLE)
        self.assertEqual(status.credits, 12.5)
        self.assertEqual(status.five_hour_percent_left, 68)
        self.assertEqual(status.weekly_percent_left, 91)
        self.assertEqual(status.five_hour_reset, "Resets in 2h 14m")

    def test_parse_claude_status(self) -> None:
        status = parse_claude_status(CLAUDE_USAGE_SAMPLE, CLAUDE_STATUS_SAMPLE)
        self.assertEqual(status.session_percent_left, 73)
        self.assertEqual(status.weekly_percent_left, 64)
        self.assertEqual(status.opus_percent_left, 82)
        self.assertEqual(status.account_email, "dave@example.com")
        self.assertEqual(status.account_organization, "Zero Delta LLC")
        self.assertEqual(status.login_method, "Claude Max 20x")

    def test_parse_codex_live_style_status(self) -> None:
        status = parse_codex_status(CODEX_LIVE_STYLE_SAMPLE)
        self.assertEqual(status.five_hour_percent_left, 96)
        self.assertEqual(status.weekly_percent_left, 92)
        self.assertEqual(status.five_hour_reset, "resets 00:15 on 14 Mar")
        self.assertEqual(status.weekly_reset, "resets 03:09 on 17 Mar")

    def test_parse_claude_live_style_status(self) -> None:
        status = parse_claude_status(CLAUDE_LIVE_STYLE_SAMPLE)
        self.assertEqual(status.session_percent_left, 30)
        self.assertEqual(status.weekly_percent_left, 52)
        self.assertRegex(status.primary_reset or "", r"^Resets 10 pm \((EST|EDT|ET)\)$")
        self.assertEqual(status.secondary_reset, "Resets Mar 17 at 4 pm")

    def test_parse_claude_compact_single_line_panel(self) -> None:
        status = parse_claude_status(CLAUDE_COMPACT_SINGLE_LINE_SAMPLE)
        self.assertEqual(status.session_percent_left, 69)
        self.assertEqual(status.weekly_percent_left, 4)
        self.assertEqual(status.opus_percent_left, 80)
        self.assertRegex(status.primary_reset or "", r"^Resets 5 pm \((EST|EDT|ET)\)$")
        self.assertRegex(
            status.secondary_reset or "", r"^Resets 10 am \((EST|EDT|ET)\)$"
        )
        self.assertRegex(
            status.opus_reset or "", r"^Resets Apr 19 at 6 pm \((EST|EDT|ET)\)$"
        )

    def test_claude_usage_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "rate limited"):
            parse_claude_status("Failed to load usage data: rate limited")

    def test_claude_subscription_plan_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "subscription plans"):
            parse_claude_status("/usage is only vilable for subscription plans.")

    def test_parse_gemini_status(self) -> None:
        status = parse_gemini_status(GEMINI_STATS_SAMPLE)
        self.assertEqual(status.flash_percent_left, 98)
        self.assertEqual(status.pro_percent_left, 83)
        self.assertEqual(status.flash_reset, "resets in 15h 36m")
        self.assertEqual(status.pro_reset, "resets in 22h 23m")
        self.assertEqual(status.account_email, "user@example.com")
        self.assertEqual(status.account_tier, "Gemini Code Assist in Google One AI Pro")

    def test_parse_copilot_status(self) -> None:
        status = parse_copilot_status(COPILOT_STATUS_SAMPLE)
        self.assertEqual(status.premium_requests, 7)
        self.assertEqual(status.sample_duration_seconds, 120)
        self.assertIsNone(status.premium_percent_left)
        self.assertIsNone(status.premium_reset)

    def test_parse_copilot_remaining_percent(self) -> None:
        status = parse_copilot_status(COPILOT_REMAINING_SAMPLE)
        self.assertIsNone(status.premium_requests)
        self.assertEqual(status.premium_percent_left, 97.6)


if __name__ == "__main__":
    unittest.main()
