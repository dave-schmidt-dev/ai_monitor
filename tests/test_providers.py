"""Provider helper tests."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from ai_monitor.providers import (
    ClaudeHttpProvider,
    CodexHttpProvider,
    CopilotHttpProvider,
    CursorProvider,
    GeminiHttpProvider,
    VibeProvider,
    ProbeFailure,
    _format_reset_time,
    _http_json,
)


class ProviderHelperTests(unittest.TestCase):
    def test_copilot_monthly_reset_label_uses_local_time(self) -> None:
        label = CopilotHttpProvider._monthly_reset_label()
        self.assertTrue(label.startswith("Resets "))
        self.assertNotIn("UTC", label)
        self.assertIn(" at ", label)


class FormatResetTimeTests(unittest.TestCase):
    def test_iso_string_with_z(self) -> None:
        result = _format_reset_time("2026-05-01T12:00:00Z")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.startswith("Resets "))
        self.assertIn(" at ", result)

    def test_iso_string_without_z(self) -> None:
        result = _format_reset_time("2026-05-01T12:00:00+00:00")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.startswith("Resets "))

    def test_epoch_seconds(self) -> None:
        # 2026-01-01 00:00:00 UTC = 1767225600
        result = _format_reset_time(1767225600)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.startswith("Resets "))

    def test_epoch_milliseconds(self) -> None:
        result = _format_reset_time(1767225600000)  # > 1e12 → treated as ms
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.startswith("Resets "))

    def test_none_input(self) -> None:
        self.assertIsNone(_format_reset_time(None))

    def test_invalid_string(self) -> None:
        self.assertIsNone(_format_reset_time("not-a-date"))


class HttpJsonHelperTests(unittest.TestCase):
    def test_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _http_json("https://example.com/api")
        self.assertEqual(result, {"ok": True})

    def test_http_error_raises_probe_failure(self) -> None:
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "https://example.com", 429, "Too Many Requests", {}, None
            ),
        ):
            with self.assertRaises(ProbeFailure) as ctx:
                _http_json("https://example.com/api")
        self.assertIn("429", str(ctx.exception))

    def test_network_error_raises_probe_failure(self) -> None:
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(ProbeFailure):
                _http_json("https://example.com/api")

    def test_invalid_json_raises_probe_failure(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(ProbeFailure):
                _http_json("https://example.com/api")


class CopilotHttpProviderTests(unittest.TestCase):
    # Real API uses percent_remaining + remaining directly; quota_reset_date_utc top-level
    PAID_RESPONSE = {
        "quota_snapshots": {
            "premium_interactions": {
                "percent_remaining": 95.0,
                "remaining": 285,
                "unlimited": False,
            }
        },
        "quota_reset_date_utc": "2026-05-01T00:00:00.000Z",
    }
    FREE_RESPONSE = {
        "quota_snapshots": {
            "premium_interactions": {
                "percent_remaining": 10.0,
                "remaining": 5,
                "unlimited": False,
            }
        },
        "quota_reset_date_utc": "2026-05-01T00:00:00.000Z",
    }

    def _make_provider(self) -> CopilotHttpProvider:
        with patch("shutil.which", return_value="/usr/bin/gh"):
            return CopilotHttpProvider()

    def test_paid_tier_field_mapping(self) -> None:
        provider = self._make_provider()
        with (
            patch("ai_monitor.providers._http_json", return_value=self.PAID_RESPONSE),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="gho_testtoken\n")
            status = provider.fetch()
        self.assertIsNotNone(status.premium_percent_left)
        assert status.premium_percent_left is not None
        self.assertAlmostEqual(status.premium_percent_left, 95.0, places=1)
        self.assertEqual(status.premium_requests, 285)
        self.assertIsNotNone(status.premium_reset)

    def test_free_tier_field_mapping(self) -> None:
        provider = self._make_provider()
        with (
            patch("ai_monitor.providers._http_json", return_value=self.FREE_RESPONSE),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="gho_testtoken\n")
            status = provider.fetch()
        self.assertIsNotNone(status.premium_percent_left)
        assert status.premium_percent_left is not None
        self.assertAlmostEqual(status.premium_percent_left, 10.0, places=1)
        self.assertEqual(status.premium_requests, 5)

    def test_401_raises_probe_failure(self) -> None:
        provider = self._make_provider()
        with (
            patch("ai_monitor.providers._http_json", side_effect=ProbeFailure("HTTP 401", "")),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="gho_testtoken\n")
            with self.assertRaises(ProbeFailure) as ctx:
                provider.fetch()
        self.assertIn("gh auth login", str(ctx.exception))


class VibeProviderTests(unittest.TestCase):
    RESPONSE = {
        "usage_percentage": 1.0841208999999998,
        "payg_enabled": False,
        "reset_at": "2026-05-01T00:00:00Z",
        "start_date": "2026-04-01T00:00:00Z",
        "end_date": "2026-04-30T23:59:59.999Z",
        "vibe": {"models": {}},
    }

    def test_usage_percentage_is_not_scaled_again(self) -> None:
        provider = VibeProvider(".")
        provider._ory_name = "ory_session_test"
        provider._ory_value = "token"
        provider._csrf = "csrf"
        body = json.dumps(self.RESPONSE).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            status = provider.fetch()
        self.assertAlmostEqual(status.usage_percent, 1.0841, places=4)
        self.assertEqual(status.start_date, "2026-04-01T00:00:00Z")
        self.assertEqual(status.end_date, "2026-04-30T23:59:59.999Z")


class CursorProviderTests(unittest.TestCase):
    USAGE_RESPONSE = {
        "billingCycleStart": 1775994366000,
        "billingCycleEnd": 1778586366000,
        "planUsage": {
            "apiPercentUsed": 1.5555555555555556,
            "autoPercentUsed": 6.644444444444445,
            "includedSpend": 369,
            "limit": 2000,
            "remaining": 1631,
            "totalPercentUsed": 4.1000000000000005,
            "totalSpend": 369,
        },
    }
    PLAN_RESPONSE = {
        "planInfo": {
            "name": "pro",
        }
    }

    def test_cursor_nested_plan_usage_mapping(self) -> None:
        provider = CursorProvider()
        provider._access_token = "cursor-access-token"
        provider._refresh_token = "cursor-refresh-token"
        with patch.object(
            provider,
            "_api_post",
            side_effect=[self.USAGE_RESPONSE, self.PLAN_RESPONSE],
        ):
            status = provider.fetch()
        self.assertAlmostEqual(status.credit_percent_left, 95.9, places=1)
        self.assertAlmostEqual(status.auto_percent_used, 6.644444444444445)
        self.assertAlmostEqual(status.api_percent_used, 1.5555555555555556)
        self.assertEqual(status.remaining_cents, 1631)
        self.assertEqual(status.limit_cents, 2000)
        self.assertEqual(status.plan_name, "pro")
        self.assertEqual(status.billing_cycle_start, "2026-04-12")
        self.assertEqual(status.billing_cycle_end_iso, "2026-05-12")


class CodexHttpProviderTests(unittest.TestCase):
    # Real API uses rate_limit.{primary,secondary}_window.used_percent (epoch reset_at)
    NORMAL_RESPONSE = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 20,
                "reset_at": 1776368464,
            },
            "secondary_window": {
                "used_percent": 20,
                "reset_at": 1776971477,
            },
        },
        "credits": {"balance": 12.5, "has_credits": True},
    }

    def _make_provider(self) -> CodexHttpProvider:
        auth_data = {
            "tokens": {
                "access_token": "test_access_token",
                "account_id": "test_account_id",
            }
        }
        with patch.object(CodexHttpProvider, "_AUTH_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps(auth_data)
            return CodexHttpProvider()

    def test_normal_response_field_mapping(self) -> None:
        provider = self._make_provider()
        with patch("ai_monitor.providers._http_json", return_value=self.NORMAL_RESPONSE):
            status = provider.fetch()
        # 100 - 20 = 80
        self.assertEqual(status.five_hour_percent_left, 80)
        self.assertEqual(status.weekly_percent_left, 80)
        self.assertAlmostEqual(status.credits, 12.5)
        self.assertIsNotNone(status.five_hour_reset)
        self.assertIsNotNone(status.weekly_reset)

    def test_401_raises_probe_failure(self) -> None:
        provider = self._make_provider()
        with patch("ai_monitor.providers._http_json", side_effect=ProbeFailure("HTTP 401", "")):
            with self.assertRaises(ProbeFailure):
                provider.fetch()


class ClaudeHttpProviderTests(unittest.TestCase):
    # Real API: {five_hour: {utilization, resets_at}, seven_day: {...}, seven_day_opus: {...}}
    NORMAL_RESPONSE = {
        "five_hour": {"utilization": 30.0, "resets_at": "2026-04-17T00:00:00Z"},
        "seven_day": {"utilization": 45.0, "resets_at": "2026-04-21T00:00:00Z"},
        "seven_day_opus": {"utilization": 10.0, "resets_at": "2026-04-21T00:00:00Z"},
    }

    def _make_provider(self) -> ClaudeHttpProvider:
        cookies = {
            "sessionKey": "sk-ant-test",
            "cf_clearance": "cf_test",
            "lastActiveOrg": "org-123",
        }
        with patch("ai_monitor.providers._read_safari_cookies", return_value=cookies):
            return ClaudeHttpProvider()

    def test_normal_response_field_mapping(self) -> None:
        provider = self._make_provider()
        with patch("ai_monitor.providers._http_json", return_value=self.NORMAL_RESPONSE):
            status = provider.fetch()
        # 100 - 30 = 70
        self.assertEqual(status.session_percent_left, 70)
        # 100 - 45 = 55
        self.assertEqual(status.weekly_percent_left, 55)
        # 100 - 10 = 90
        self.assertEqual(status.opus_percent_left, 90)
        self.assertIsNone(status.account_email)
        self.assertIsNone(status.account_organization)
        self.assertIsNone(status.login_method)

    def test_401_raises_probe_failure(self) -> None:
        provider = self._make_provider()
        with patch("ai_monitor.providers._http_json", side_effect=ProbeFailure("HTTP 401", "")):
            with self.assertRaises(ProbeFailure) as ctx:
                provider.fetch()
        self.assertIn("session expired", str(ctx.exception).lower())

    def test_missing_cookies_raises_probe_failure(self) -> None:
        # Patches must stay active through fetch() so _load_cookies() still finds nothing
        with (
            patch("ai_monitor.providers._read_safari_cookies", return_value={}),
            patch("subprocess.Popen"),
        ):
            provider = ClaudeHttpProvider()
            with self.assertRaises(ProbeFailure) as ctx:
                provider.fetch()
        self.assertIn("claude.ai", str(ctx.exception).lower())


class GeminiHttpProviderTests(unittest.TestCase):
    QUOTA_RESPONSE = {
        "buckets": [
            {
                "modelId": "gemini-2.5-flash",
                "remainingFraction": 0.75,
                "resetTime": "2026-04-21T00:00:00Z",
            },
            {
                "modelId": "gemini-2.5-pro",
                "remainingFraction": 0.40,
                "resetTime": "2026-04-21T00:00:00Z",
            },
        ],
        "tier": "STANDARD",
    }

    def _make_provider(self) -> GeminiHttpProvider:
        creds = {
            "access_token": "ya29.test",
            "refresh_token": "1//test",
            "expiry_date": int(datetime.now().timestamp() * 1000) + 3_600_000,  # 1h from now
        }
        projects = {"projects": {"/test/cwd": "test-project"}}
        with (
            patch.object(GeminiHttpProvider, "_CREDS_PATH") as mock_creds_path,
            patch.object(GeminiHttpProvider, "_PROJECTS_PATH") as mock_proj_path,
        ):
            mock_creds_path.exists.return_value = True
            mock_creds_path.read_text.return_value = json.dumps(creds)
            mock_proj_path.read_text.return_value = json.dumps(projects)
            return GeminiHttpProvider("/test/cwd")

    def test_flash_and_pro_buckets(self) -> None:
        provider = self._make_provider()
        # loadCodeAssist returns empty dict, retrieveUserQuota returns quota
        with patch(
            "ai_monitor.providers._http_json",
            side_effect=[{}, self.QUOTA_RESPONSE],
        ):
            status = provider.fetch()
        self.assertEqual(status.flash_percent_left, 75)
        self.assertEqual(status.pro_percent_left, 40)
        self.assertIsNotNone(status.flash_reset)
        self.assertIsNotNone(status.pro_reset)
        self.assertEqual(status.account_tier, "STANDARD")

    def test_token_not_refreshed_when_valid(self) -> None:
        """Token with future expiry should not trigger refresh."""
        provider = self._make_provider()
        with patch(
            "ai_monitor.providers._http_json", side_effect=[{}, self.QUOTA_RESPONSE]
        ) as mock_http:
            status = provider.fetch()
        # loadCodeAssist + retrieveUserQuota = 2 calls, no refresh call
        self.assertEqual(mock_http.call_count, 2)
        self.assertEqual(status.flash_percent_left, 75)

    def test_expired_token_refresh_includes_client_credentials(self) -> None:
        """Token refresh must send client_id and client_secret from creds file."""
        creds = {
            "access_token": "ya29.expired",
            "refresh_token": "1//test-refresh",
            "client_id": "test-client-id.apps.googleusercontent.com",
            "client_secret": "GOCSPX-test-secret",
            "expiry_date": 0,  # long expired
        }
        projects = {"projects": {"/test/cwd": "test-project"}}
        refresh_response = {
            "access_token": "ya29.refreshed",
            "expires_in": 3599,
        }
        with (
            patch.object(GeminiHttpProvider, "_CREDS_PATH") as mock_creds_path,
            patch.object(GeminiHttpProvider, "_PROJECTS_PATH") as mock_proj_path,
        ):
            mock_creds_path.exists.return_value = True
            mock_creds_path.read_text.return_value = json.dumps(creds)
            mock_creds_path.write_text = MagicMock()
            mock_proj_path.read_text.return_value = json.dumps(projects)
            provider = GeminiHttpProvider("/test/cwd")

            # refresh + loadCodeAssist + retrieveUserQuota = 3 calls
            # Keep path mocks active — _maybe_refresh re-reads creds from disk
            with patch(
                "ai_monitor.providers._http_json",
                side_effect=[refresh_response, {}, self.QUOTA_RESPONSE],
            ) as mock_http:
                status = provider.fetch()

        # First call should be the refresh with client credentials from creds file
        refresh_call = mock_http.call_args_list[0]
        refresh_body = json.loads(refresh_call[1]["body"])
        self.assertEqual(refresh_body["client_id"], "test-client-id.apps.googleusercontent.com")
        self.assertEqual(refresh_body["client_secret"], "GOCSPX-test-secret")
        self.assertEqual(refresh_body["refresh_token"], "1//test-refresh")
        self.assertEqual(status.flash_percent_left, 75)

    def test_missing_client_credentials_extracts_from_cli(self) -> None:
        """When creds file lacks client_id, extraction from CLI bundle is attempted."""
        creds = {
            "access_token": "ya29.test",
            "refresh_token": "1//test",
            "expiry_date": int(datetime.now().timestamp() * 1000) + 3_600_000,
        }
        projects = {"projects": {"/test/cwd": "test-project"}}
        extracted = ("extracted-id.apps.googleusercontent.com", "GOCSPX-extracted")
        with (
            patch.object(GeminiHttpProvider, "_CREDS_PATH") as mock_creds_path,
            patch.object(GeminiHttpProvider, "_PROJECTS_PATH") as mock_proj_path,
            patch.object(
                GeminiHttpProvider,
                "_extract_client_credentials_from_cli",
                return_value=extracted,
            ),
        ):
            mock_creds_path.exists.return_value = True
            mock_creds_path.read_text.return_value = json.dumps(creds)
            mock_creds_path.write_text = MagicMock()
            mock_proj_path.read_text.return_value = json.dumps(projects)
            provider = GeminiHttpProvider("/test/cwd")

        self.assertEqual(provider._creds["client_id"], "extracted-id.apps.googleusercontent.com")
        self.assertEqual(provider._creds["client_secret"], "GOCSPX-extracted")
        # Should have written the updated creds back to disk
        mock_creds_path.write_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()
