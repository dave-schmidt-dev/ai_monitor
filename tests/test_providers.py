"""Provider helper tests."""

from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_monitor.providers import (
    ClaudeHttpProvider,
    CodexHttpProvider,
    CopilotHttpProvider,
    CursorProvider,
    GeminiHttpProvider,
    ProbeFailure,
    VibeProvider,
    _format_reset_time,
    _http_json,
    _is_jwt_expired,
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

    def test_post_rebrand_response_derives_cycle_boundaries(self) -> None:
        # 2026-05-28 Le Chat → Vibe rebrand dropped start_date/end_date.
        rebrand_response = {
            "usage_percentage": 9.089239716666667,
            "quota_changed_this_month": False,
            "payg_enabled": False,
            "reset_at": "2026-06-01T00:00:00Z",
        }
        provider = VibeProvider(".")
        provider._ory_name = "ory_session_test"
        provider._ory_value = "token"
        provider._csrf = "csrf"
        body = json.dumps(rebrand_response).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            status = provider.fetch()
        self.assertAlmostEqual(status.usage_percent, 9.0892, places=4)
        self.assertEqual(status.start_date, "2026-05-01T00:00:00+00:00")
        self.assertEqual(status.end_date, "2026-06-01T00:00:00+00:00")

    def test_year_rollover_derives_previous_december_start(self) -> None:
        # reset_at in January should yield start_date = Dec 1 of previous year.
        rollover_response = {
            "usage_percentage": 12.5,
            "payg_enabled": False,
            "reset_at": "2027-01-01T00:00:00Z",
        }
        provider = VibeProvider(".")
        provider._ory_name = "ory_session_test"
        provider._ory_value = "token"
        provider._csrf = "csrf"
        body = json.dumps(rollover_response).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            status = provider.fetch()
        self.assertEqual(status.start_date, "2026-12-01T00:00:00+00:00")
        self.assertEqual(status.end_date, "2027-01-01T00:00:00+00:00")

    def test_missing_reset_at_leaves_boundaries_none(self) -> None:
        # Without reset_at there is no anchor to derive from — both fields stay None.
        no_reset_response = {
            "usage_percentage": 4.2,
            "payg_enabled": False,
        }
        provider = VibeProvider(".")
        provider._ory_name = "ory_session_test"
        provider._ory_value = "token"
        provider._csrf = "csrf"
        body = json.dumps(no_reset_response).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            status = provider.fetch()
        self.assertAlmostEqual(status.usage_percent, 4.2, places=4)
        self.assertIsNone(status.start_date)
        self.assertIsNone(status.end_date)
        self.assertIsNone(status.reset_at)


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
        self.assertAlmostEqual(status.credit_percent_left, 81.55, places=2)
        self.assertAlmostEqual(status.auto_percent_used, 6.644444444444445)
        self.assertAlmostEqual(status.api_percent_used, 1.5555555555555556)
        self.assertEqual(status.remaining_cents, 1631)
        self.assertEqual(status.limit_cents, 2000)
        self.assertEqual(status.plan_name, "pro")
        self.assertEqual(status.billing_cycle_start, "2026-04-12")
        self.assertEqual(status.billing_cycle_end_iso, "2026-05-12")

    def test_cursor_falls_back_to_total_percent_used_when_cents_missing(self) -> None:
        provider = CursorProvider()
        provider._access_token = "cursor-access-token"
        provider._refresh_token = "cursor-refresh-token"
        usage_response = {
            "billingCycleStart": 1775994366000,
            "billingCycleEnd": 1778586366000,
            "planUsage": {
                "totalPercentUsed": 4.1,
            },
        }
        with patch.object(
            provider,
            "_api_post",
            side_effect=[usage_response, self.PLAN_RESPONSE],
        ):
            status = provider.fetch()
        self.assertAlmostEqual(status.credit_percent_left, 95.9, places=1)


def _make_jwt(exp_offset_seconds: int) -> str:
    """Build a fake JWT whose `exp` claim is now + offset (no signature)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_obj = {"exp": int(time.time()) + exp_offset_seconds}
    payload = base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}."


class JwtExpiryTests(unittest.TestCase):
    def test_unexpired_token(self) -> None:
        self.assertFalse(_is_jwt_expired(_make_jwt(3600)))

    def test_expired_token(self) -> None:
        self.assertTrue(_is_jwt_expired(_make_jwt(-3600)))

    def test_within_leeway_treated_as_expired(self) -> None:
        # leeway is 60s; expiring in 30s counts as expired
        self.assertTrue(_is_jwt_expired(_make_jwt(30)))

    def test_non_jwt_returns_false(self) -> None:
        self.assertFalse(_is_jwt_expired("not-a-jwt"))

    def test_jwt_without_exp_returns_false(self) -> None:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode()
        self.assertFalse(_is_jwt_expired(f"{header}.{payload}."))


class CursorTokenCacheTests(unittest.TestCase):
    """Regression: aimonitor must keep working when Safari has lost the cookie."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_path = Path(self._tmpdir.name) / "cursor_token.json"
        # Patch class attribute so provider instances use the tempfile.
        self._patcher = patch.object(CursorProvider, "_CACHE_PATH", self._cache_path)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_cache_used_when_safari_empty(self) -> None:
        """If cache has a valid token, Safari is not consulted and no browser opens."""
        valid_jwt = _make_jwt(3600)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps({"access_token": valid_jwt, "refresh_token": "rt"}),
            encoding="utf-8",
        )
        with (
            patch("ai_monitor.providers._read_safari_cookies", return_value={}) as safari,
            patch("ai_monitor.providers.subprocess.Popen") as popen,
        ):
            provider = CursorProvider()
        self.assertEqual(provider._access_token, valid_jwt)
        self.assertEqual(provider._refresh_token, "rt")
        self.assertEqual(provider._token_source, "cache")
        safari.assert_not_called()
        popen.assert_not_called()

    def test_expired_cache_falls_back_to_safari(self) -> None:
        """Expired cached token is ignored; Safari is consulted instead."""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps({"access_token": _make_jwt(-3600)}),
            encoding="utf-8",
        )
        fresh_jwt = _make_jwt(3600)
        cookie_value = f"user_x%3A%3A{fresh_jwt}"
        with patch(
            "ai_monitor.providers._read_safari_cookies",
            return_value={"WorkosCursorSessionToken": cookie_value},
        ):
            provider = CursorProvider()
        self.assertEqual(provider._access_token, fresh_jwt)
        self.assertEqual(provider._token_source, "safari")

    def test_safari_read_writes_cache(self) -> None:
        """First Safari read persists the token for subsequent runs."""
        fresh_jwt = _make_jwt(3600)
        cookie_value = f"user_x%3A%3A{fresh_jwt}"
        with patch(
            "ai_monitor.providers._read_safari_cookies",
            return_value={"WorkosCursorSessionToken": cookie_value},
        ):
            CursorProvider()
        self.assertTrue(self._cache_path.exists())
        cached = json.loads(self._cache_path.read_text(encoding="utf-8"))
        self.assertEqual(cached["access_token"], fresh_jwt)

    def test_401_clears_cache(self) -> None:
        """A rejected token must be evicted so the next startup re-reads from Safari."""
        import urllib.error as ue

        valid_jwt = _make_jwt(3600)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps({"access_token": valid_jwt}), encoding="utf-8")
        with patch("ai_monitor.providers._read_safari_cookies", return_value={}):
            provider = CursorProvider()
        err = ue.HTTPError("u", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
        with patch.object(provider, "_api_post", side_effect=err):
            with self.assertRaises(ProbeFailure):
                provider.fetch()
        self.assertFalse(self._cache_path.exists())


class ClaudeCookieCacheTests(unittest.TestCase):
    """Same disk-sync-lag fix as Cursor: cache Safari cookies to local file."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_path = Path(self._tmpdir.name) / "claude_cookies.json"
        self._patcher = patch.object(ClaudeHttpProvider, "_CACHE_PATH", self._cache_path)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_cache_used_when_safari_empty(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps({"sessionKey": "sk", "cf_clearance": "cf", "lastActiveOrg": "org"}),
            encoding="utf-8",
        )
        with (
            patch("ai_monitor.providers._read_safari_cookies", return_value={}) as safari,
            patch("ai_monitor.providers.subprocess.Popen") as popen,
        ):
            provider = ClaudeHttpProvider()
        self.assertEqual(provider._session_key, "sk")
        self.assertEqual(provider._cf_clearance, "cf")
        self.assertEqual(provider._org_id, "org")
        safari.assert_not_called()
        popen.assert_not_called()

    def test_safari_read_writes_cache(self) -> None:
        with patch(
            "ai_monitor.providers._read_safari_cookies",
            return_value={"sessionKey": "sk", "cf_clearance": "cf", "lastActiveOrg": "org"},
        ):
            ClaudeHttpProvider()
        self.assertTrue(self._cache_path.exists())
        cached = json.loads(self._cache_path.read_text(encoding="utf-8"))
        self.assertEqual(cached["sessionKey"], "sk")
        self.assertEqual(cached["lastActiveOrg"], "org")

    def test_403_clears_cache(self) -> None:
        """cf_clearance can expire fast; a 403 must evict the cache to recover."""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps({"sessionKey": "sk", "cf_clearance": "cf", "lastActiveOrg": "org"}),
            encoding="utf-8",
        )
        with patch("ai_monitor.providers._read_safari_cookies", return_value={}):
            provider = ClaudeHttpProvider()
        with patch(
            "ai_monitor.providers._http_json",
            side_effect=ProbeFailure("Claude API returned HTTP 403", ""),
        ):
            with self.assertRaises(ProbeFailure):
                provider.fetch()
        self.assertFalse(self._cache_path.exists())


class VibeCookieCacheTests(unittest.TestCase):
    """Same disk-sync-lag fix as Cursor: cache Mistral cookies to local file."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_path = Path(self._tmpdir.name) / "vibe_cookies.json"
        self._patcher = patch.object(VibeProvider, "_CACHE_PATH", self._cache_path)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_cache_used_when_safari_and_chrome_empty(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps(
                {"ory_session_name": "ory_session_x", "ory_session_value": "v", "csrftoken": "c"}
            ),
            encoding="utf-8",
        )
        with (
            patch.object(VibeProvider, "_extract_safari_cookies", return_value=None) as safari,
            patch.object(VibeProvider, "_extract_chrome_cookies", return_value=None) as chrome,
            patch("ai_monitor.providers.subprocess.Popen") as popen,
        ):
            provider = VibeProvider(project_root=self._tmpdir.name)
        self.assertEqual(provider._ory_name, "ory_session_x")
        self.assertEqual(provider._ory_value, "v")
        self.assertEqual(provider._csrf, "c")
        safari.assert_not_called()
        chrome.assert_not_called()
        popen.assert_not_called()

    def test_safari_read_writes_cache(self) -> None:
        with patch.object(
            VibeProvider,
            "_extract_safari_cookies",
            return_value={
                "ory_session_name": "ory_session_x",
                "ory_session_value": "v",
                "csrftoken": "c",
            },
        ):
            VibeProvider(project_root=self._tmpdir.name)
        self.assertTrue(self._cache_path.exists())
        cached = json.loads(self._cache_path.read_text(encoding="utf-8"))
        self.assertEqual(cached["ory_session_name"], "ory_session_x")
        self.assertEqual(cached["csrftoken"], "c")

    def test_401_clears_cache(self) -> None:
        import urllib.error as ue

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps(
                {"ory_session_name": "ory_session_x", "ory_session_value": "v", "csrftoken": "c"}
            ),
            encoding="utf-8",
        )
        with (
            patch.object(VibeProvider, "_extract_safari_cookies", return_value=None),
            patch.object(VibeProvider, "_extract_chrome_cookies", return_value=None),
        ):
            provider = VibeProvider(project_root=self._tmpdir.name)
        err = ue.HTTPError("u", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ProbeFailure):
                provider.fetch()
        self.assertFalse(self._cache_path.exists())


class CacheResilienceTests(unittest.TestCase):
    """Edge-cases: corrupted cache files and write failures must not break providers."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cursor_cache = Path(self._tmpdir.name) / "cursor_token.json"
        self._claude_cache = Path(self._tmpdir.name) / "claude_cookies.json"
        self._vibe_cache = Path(self._tmpdir.name) / "vibe_cookies.json"
        self._patchers = [
            patch.object(CursorProvider, "_CACHE_PATH", self._cursor_cache),
            patch.object(ClaudeHttpProvider, "_CACHE_PATH", self._claude_cache),
            patch.object(VibeProvider, "_CACHE_PATH", self._vibe_cache),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()
        self._tmpdir.cleanup()

    def test_cursor_corrupted_cache_falls_back_to_safari(self) -> None:
        """Garbage JSON in cursor cache is silently ignored; Safari is consulted."""
        self._cursor_cache.parent.mkdir(parents=True, exist_ok=True)
        self._cursor_cache.write_text("{ NOT VALID JSON !!!", encoding="utf-8")
        fresh_jwt = _make_jwt(3600)
        cookie_value = f"user_x%3A%3A{fresh_jwt}"
        with patch(
            "ai_monitor.providers._read_safari_cookies",
            return_value={"WorkosCursorSessionToken": cookie_value},
        ):
            provider = CursorProvider()
        self.assertEqual(provider._access_token, fresh_jwt)
        self.assertEqual(provider._token_source, "safari")

    def test_claude_corrupted_cache_falls_back_to_safari(self) -> None:
        """Garbage JSON in Claude cache is silently ignored; Safari is consulted."""
        self._claude_cache.parent.mkdir(parents=True, exist_ok=True)
        self._claude_cache.write_text("{ NOT VALID JSON !!!", encoding="utf-8")
        with patch(
            "ai_monitor.providers._read_safari_cookies",
            return_value={"sessionKey": "sk", "cf_clearance": "cf", "lastActiveOrg": "org"},
        ):
            provider = ClaudeHttpProvider()
        self.assertEqual(provider._session_key, "sk")
        self.assertEqual(provider._org_id, "org")

    def test_vibe_corrupted_cache_falls_back_to_safari(self) -> None:
        """Garbage JSON in Vibe cache is silently ignored; Safari is consulted."""
        self._vibe_cache.parent.mkdir(parents=True, exist_ok=True)
        self._vibe_cache.write_text("{ NOT VALID JSON !!!", encoding="utf-8")
        with patch.object(
            VibeProvider,
            "_extract_safari_cookies",
            return_value={
                "ory_session_name": "ory_session_x",
                "ory_session_value": "v",
                "csrftoken": "c",
            },
        ):
            provider = VibeProvider(project_root=self._tmpdir.name)
        self.assertEqual(provider._ory_name, "ory_session_x")

    def test_cursor_save_cache_oserror_does_not_break_provider(self) -> None:
        """If writing the cache raises OSError, the provider still has a valid token."""
        fresh_jwt = _make_jwt(3600)
        cookie_value = f"user_x%3A%3A{fresh_jwt}"
        with (
            patch(
                "ai_monitor.providers._read_safari_cookies",
                return_value={"WorkosCursorSessionToken": cookie_value},
            ),
            patch("pathlib.Path.mkdir", side_effect=OSError("no space")),
        ):
            provider = CursorProvider()
        self.assertEqual(provider._access_token, fresh_jwt)

    def test_cursor_refresh_writes_new_refresh_token_to_cache(self) -> None:
        """After a token refresh, the updated refresh_token must be persisted."""
        import urllib.error as ue

        valid_jwt = _make_jwt(3600)
        self._cursor_cache.parent.mkdir(parents=True, exist_ok=True)
        self._cursor_cache.write_text(
            json.dumps({"access_token": valid_jwt, "refresh_token": "old_rt"}),
            encoding="utf-8",
        )
        with patch("ai_monitor.providers._read_safari_cookies", return_value={}):
            provider = CursorProvider()

        # Simulate: first API call → 401, refresh succeeds with new tokens, retry succeeds
        first_err = ue.HTTPError("u", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

        new_jwt = _make_jwt(7200)
        refresh_body = json.dumps({"access_token": new_jwt, "refresh_token": "new_rt"}).encode()
        mock_refresh_resp = MagicMock()
        mock_refresh_resp.read.return_value = refresh_body
        mock_refresh_resp.__enter__ = lambda s: s
        mock_refresh_resp.__exit__ = MagicMock(return_value=False)

        usage_resp = {
            "billingCycleStart": 1775994366000,
            "billingCycleEnd": 1778586366000,
            "planUsage": {"totalPercentUsed": 4.1},
        }
        plan_resp = {"planInfo": {"name": "pro"}}

        call_count = 0

        def api_post_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise first_err
            elif call_count == 2:
                return usage_resp
            else:
                return plan_resp

        with (
            patch.object(provider, "_api_post", side_effect=api_post_side_effect),
            patch("urllib.request.urlopen", return_value=mock_refresh_resp),
        ):
            provider.fetch()

        # Cache must now contain the new refresh token
        cached = json.loads(self._cursor_cache.read_text(encoding="utf-8"))
        self.assertEqual(cached["access_token"], new_jwt)
        self.assertEqual(cached["refresh_token"], "new_rt")


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
