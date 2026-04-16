"""Provider probes for Codex, Claude, Gemini, Copilot, and Cursor usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from zoneinfo import ZoneInfo


from .parsing import (
    ClaudeStatus,
    CopilotStatus,
    CodexStatus,
    CursorStatus,
    GeminiStatus,
    VibeStatus,
    parse_claude_status,
    parse_copilot_status,
    parse_codex_status,
    parse_gemini_status,
    strip_ansi,
)
from .pty_session import CaptureConfig, PersistentPTYSession

log = logging.getLogger(__name__)


TRUST_PROMPTS = (
    ("Do you trust the files in this folder?", "y\r"),
    ("Quick safety check:", "\r"),
    ("Yes, I trust this folder", "\r"),
    ("Ready to code here?", "\r"),
    ("Press Enter to continue", "\r"),
)


def _is_empty_or_echo(raw_text: str, command: str) -> bool:
    cleaned = strip_ansi(raw_text).strip()
    if not cleaned:
        return True
    compact_lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return bool(compact_lines) and all(line == command for line in compact_lines)


def _is_terminal_probe_noise(raw_text: str) -> bool:
    cleaned = strip_ansi(raw_text).strip()
    if not cleaned:
        return True
    compact = "".join(cleaned.split())
    if not compact:
        return True
    return bool(re.fullmatch(r"[0-9;?]{2,16}", compact))


@dataclass(slots=True)
class ProviderSnapshot:
    name: str
    ok: bool
    source: str
    data: dict[str, Any] | None = None
    error: str | None = None
    cached_since: datetime | None = None


class ProbeFailure(RuntimeError):
    """Raised when a provider captured output but parsing still failed."""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


def _debug_dump_path(name: str) -> Path:
    safe_name = name.lower().replace(" ", "_")
    return Path("/tmp") / f"ai_monitor_{safe_name}_capture.txt"


def _write_debug_dump(name: str, raw_text: str) -> None:
    _debug_dump_path(name).write_text(raw_text, encoding="utf-8")


class CodexProvider:
    """Fetch usage from the local Codex CLI."""

    def __init__(self, cwd: str) -> None:
        binary = shutil.which("codex")
        if not binary:
            raise FileNotFoundError("codex not found on PATH")
        self.session = PersistentPTYSession(
            binary=binary,
            args=["--no-alt-screen", "-s", "read-only", "-a", "untrusted"],
            cwd=cwd,
        )

    def fetch(self) -> CodexStatus:
        last_error: Exception | None = None
        last_raw = ""
        for attempt in range(3):
            _ = self.session.capture(
                "",
                CaptureConfig(
                    timeout=4.0,
                    startup_wait=0.8 if attempt == 0 else 1.2,
                    idle_timeout=1.0,
                    discard_preexisting_output=False,
                ),
            )
            raw = self.session.capture(
                "/status",
                CaptureConfig(
                    timeout=18.0,
                    startup_wait=1.2 if attempt == 0 else 1.8,
                    idle_timeout=3.5,
                    stop_substrings=(
                        "Credits:",
                        "5h limit",
                        "5-hour limit",
                        "Weekly limit",
                    ),
                    settle_after_stop=1.5,
                    send_enter_every=1.4,
                    resend_command_every=4.0,
                    resend_command_max=3,
                ),
            )
            if _is_empty_or_echo(raw, "/status") or _is_terminal_probe_noise(raw):
                last_error = ValueError("empty Codex output")
                last_raw = raw
                self.session.close()
                continue
            try:
                return parse_codex_status(raw)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                last_raw = raw
                if (
                    "empty" in str(exc).lower()
                    or "data not available yet" in str(exc).lower()
                    or _is_terminal_probe_noise(raw)
                ):
                    self.session.close()
                    continue
                raise ProbeFailure(str(exc), strip_ansi(raw)) from exc
        assert last_error is not None
        raise ProbeFailure(str(last_error), strip_ansi(last_raw)) from last_error

    def close(self) -> None:
        self.session.close()


class ClaudeProvider:
    """Fetch usage from the local Claude CLI."""

    def __init__(self, cwd: str) -> None:
        binary = shutil.which("claude")
        if not binary:
            raise FileNotFoundError("claude not found on PATH")
        self.session = PersistentPTYSession(binary=binary, args=[], cwd=cwd)

    def fetch(self) -> ClaudeStatus:
        last_error: Exception | None = None
        last_combined = ""
        for attempt in range(2):
            warmup_raw = self.session.capture(
                "",
                CaptureConfig(
                    timeout=4.0 if attempt == 0 else 5.0,
                    startup_wait=0.8,
                    idle_timeout=0.9,
                    discard_preexisting_output=False,
                    auto_responses=TRUST_PROMPTS,
                ),
            )
            usage_raw = self.session.capture(
                "/usage",
                CaptureConfig(
                    timeout=24.0,
                    startup_wait=2.0 if attempt == 0 else 2.4,
                    idle_timeout=None,
                    stop_substrings=(
                        "Current session",
                        "Current week (all models)",
                        "Failed to load usage data",
                        "failed to load usage data",
                        "failedtoloadusagedata",
                        "/usage is only",
                        "/usageisonly",
                    ),
                    settle_after_stop=2.0,
                    send_enter_every=0.8,
                    resend_command_every=5.0,
                    resend_command_max=1,
                    auto_responses=TRUST_PROMPTS
                    + (("Show plan usage limits", "\r"), ("Show plan", "\r")),
                ),
            )
            if _is_empty_or_echo(usage_raw, "/usage"):
                last_error = ValueError("empty Claude output")
                last_combined = (
                    f"--- warmup ---\n{strip_ansi(warmup_raw)}\n"
                    f"--- usage ---\n{strip_ansi(usage_raw)}\n--- status ---\n"
                )
                self.session.close()
                continue
            status_raw = self.session.capture(
                "/status",
                CaptureConfig(
                    timeout=12.0,
                    startup_wait=0.5,
                    idle_timeout=3.0,
                    settle_after_stop=0.5,
                    resend_command_every=4.0,
                    resend_command_max=1,
                    auto_responses=TRUST_PROMPTS
                    + (("Show Claude Code status", "\r"), ("Show Claude Code", "\r")),
                ),
            )
            try:
                return parse_claude_status(usage_raw, status_raw)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                last_combined = (
                    f"--- warmup ---\n{strip_ansi(warmup_raw)}\n"
                    f"--- usage ---\n{strip_ansi(usage_raw)}\n--- status ---\n{strip_ansi(status_raw)}"
                )
                message = str(exc).lower()
                if "empty" in message or "missing current session" in message:
                    self.session.close()
                    continue
                raise ProbeFailure(str(exc), last_combined) from exc
        assert last_error is not None
        raise ProbeFailure(str(last_error), last_combined) from last_error

    def close(self) -> None:
        self.session.close()


class GeminiProvider:
    """Fetch usage from the local Gemini CLI."""

    def __init__(self, cwd: str) -> None:
        binary = shutil.which("gemini")
        if not binary:
            raise FileNotFoundError("gemini not found on PATH")
        self.binary = binary
        self.cwd = cwd
        self.session = PersistentPTYSession(binary=binary, args=[], cwd=cwd)

    def fetch(self) -> GeminiStatus:
        direct = self._fetch_via_internal_quota_probe()
        if direct is not None:
            return direct

        last_error: Exception | None = None
        last_raw = ""
        for attempt in range(2):
            raw = self.session.capture(
                "/stats",
                CaptureConfig(
                    timeout=10.0 if attempt == 0 else 14.0,
                    startup_wait=1.8 if attempt == 0 else 3.0,
                    idle_timeout=2.2,
                    stop_substrings=(
                        "Session Stats",
                        "Usage remaining",
                        "gemini-2.5-pro",
                        "gemini-3.1-pro-preview",
                    ),
                    settle_after_stop=1.0,
                    auto_responses=TRUST_PROMPTS,
                ),
            )
            if _is_empty_or_echo(raw, "/stats"):
                last_error = ValueError("empty Gemini output")
                last_raw = raw
                self.session.close()
                continue
            if self._is_waiting_for_authentication(raw):
                message = "Gemini CLI is waiting for authentication; run `gemini` once and finish sign-in, then rerun ai_monitor."
                raise ProbeFailure(message, strip_ansi(raw))
            try:
                return parse_gemini_status(raw)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                last_raw = raw
                if (
                    "empty" in str(exc).lower()
                    or "could not find gemini session stats panel" in str(exc).lower()
                ):
                    self.session.close()
                    continue
                raise ProbeFailure(str(exc), strip_ansi(raw)) from exc
        assert last_error is not None
        raise ProbeFailure(str(last_error), strip_ansi(last_raw)) from last_error

    def _fetch_via_internal_quota_probe(self) -> GeminiStatus | None:
        node = shutil.which("node")
        if not node:
            return None
        module_root = self._gemini_module_root()
        if module_root is None:
            return None
        bundle_status = self._fetch_via_bundle_quota_probe(node, module_root)
        if bundle_status is not None:
            return bundle_status
        return self._fetch_via_legacy_dist_probe(node, module_root)

    def _fetch_via_bundle_quota_probe(
        self, node: str, module_root: Path
    ) -> GeminiStatus | None:
        core_module = self._gemini_bundle_core_module(module_root)
        if core_module is None:
            return None
        script = """
import { makeFakeConfig, AuthType } from '__CORE_MODULE__';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

let selectedType = AuthType.LOGIN_WITH_GOOGLE;
try {
  const settingsPath = join(process.env.HOME || '', '.gemini', 'settings.json');
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
  selectedType = settings?.security?.auth?.selectedType || selectedType;
} catch {}

const config = makeFakeConfig({ cwd: process.cwd(), targetDir: process.cwd() });
await config.refreshAuth(selectedType);
const quota = await config.refreshUserQuota();
console.log(JSON.stringify({
  tier: typeof config.getUserTierName === 'function' ? config.getUserTierName() : null,
  pooledRemaining: typeof config.getQuotaRemaining === 'function' ? config.getQuotaRemaining() : null,
  pooledLimit: typeof config.getQuotaLimit === 'function' ? config.getQuotaLimit() : null,
  pooledResetTime: typeof config.getQuotaResetTime === 'function' ? config.getQuotaResetTime() : null,
  buckets: quota?.buckets?.map((bucket) => ({
    modelId: bucket.modelId,
    remainingFraction: bucket.remainingFraction,
    resetTime: bucket.resetTime
  })) ?? []
}));
""".strip().replace("__CORE_MODULE__", core_module.as_posix())

        try:
            result = subprocess.run(
                [node, "--input-type=module", "-e", script],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=18,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        payload = self._extract_json_line(result.stdout or "")
        if payload is None:
            return None
        return self._gemini_status_from_payload(payload)

    def _fetch_via_legacy_dist_probe(
        self, node: str, module_root: Path
    ) -> GeminiStatus | None:
        script = """
import { loadSettings } from '__MODULE_ROOT__/dist/src/config/settings.js';
import { loadCliConfig } from '__MODULE_ROOT__/dist/src/config/config.js';
const settings = loadSettings();
const argv = { prompt: '', query: undefined, debug: false, outputFormat: 'json', approvalMode: 'default' };
const config = await loadCliConfig(settings.merged, 'ai-monitor-probe', argv, { cwd: process.cwd() });
const authType = settings.merged.security?.auth?.selectedType;
await config.refreshAuth(authType);
const quota = await config.refreshUserQuota();
await config.refreshAvailableCredits();
console.log(JSON.stringify({
  tier: config.getUserTierName(),
  pooledRemaining: config.getQuotaRemaining(),
  pooledLimit: config.getQuotaLimit(),
  pooledResetTime: config.getQuotaResetTime(),
  buckets: quota?.buckets?.map((bucket) => ({
    modelId: bucket.modelId,
    remainingFraction: bucket.remainingFraction,
    resetTime: bucket.resetTime
  })) ?? []
}));
""".strip().replace("__MODULE_ROOT__", module_root.as_posix())

        try:
            result = subprocess.run(
                [node, "--input-type=module", "-e", script],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if result.returncode != 0 or not result.stdout.strip():
            return None

        payload = self._extract_json_line(result.stdout)
        if payload is None:
            return None
        return self._gemini_status_from_payload(payload)

    def _gemini_status_from_payload(
        self, payload: dict[str, Any]
    ) -> GeminiStatus | None:
        buckets = payload.get("buckets") or []
        if not buckets:
            return None

        flash_bucket = self._find_bucket(
            buckets,
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        )
        pro_bucket = self._find_bucket(
            buckets,
            "gemini-3.1-pro-preview",
            "gemini-3-pro-preview",
            "gemini-2.5-pro",
        )
        if not flash_bucket and not pro_bucket:
            return None

        account_email = self._read_gemini_account_email()
        raw_text = json.dumps(payload, indent=2, sort_keys=True)
        return GeminiStatus(
            flash_percent_left=self._percent_from_fraction(flash_bucket),
            pro_percent_left=self._percent_from_fraction(pro_bucket),
            flash_reset=self._reset_from_iso(flash_bucket["resetTime"])
            if flash_bucket
            else None,
            pro_reset=self._reset_from_iso(pro_bucket["resetTime"])
            if pro_bucket
            else None,
            account_email=account_email,
            account_tier=payload.get("tier"),
            raw_text=raw_text,
        )

    @staticmethod
    def _gemini_bundle_core_module(module_root: Path) -> Path | None:
        bundle_dir = module_root / "bundle"
        if not bundle_dir.exists():
            return None
        for candidate in bundle_dir.glob("chunk-*.js"):
            try:
                text = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if (
                "makeFakeConfig" in text
                and "refreshUserQuota" in text
                and "AuthType" in text
            ):
                return candidate
        return None

    @staticmethod
    def _gemini_module_root_from_binary(binary: str) -> Path | None:
        resolved = Path(binary).resolve()
        candidates = (
            resolved.parent.parent
            if resolved.name == "gemini.js" and resolved.parent.name == "bundle"
            else None,
            resolved.parent.parent
            if resolved.name == "index.js" and resolved.parent.name == "dist"
            else None,
            resolved.parent.parent
            / "libexec"
            / "lib"
            / "node_modules"
            / "@google"
            / "gemini-cli",
            resolved.parent.parent.parent
            / "libexec"
            / "lib"
            / "node_modules"
            / "@google"
            / "gemini-cli",
        )
        for candidate in candidates:
            if candidate is not None and candidate.exists():
                return candidate
        return None

    def _gemini_module_root(self) -> Path | None:
        return self._gemini_module_root_from_binary(self.binary)

    @staticmethod
    def _find_bucket(
        buckets: list[dict[str, Any]], *model_ids: str
    ) -> dict[str, Any] | None:
        for model_id in model_ids:
            for bucket in buckets:
                if bucket.get("modelId") == model_id:
                    return bucket
        return None

    @staticmethod
    def _extract_json_line(text: str) -> dict[str, Any] | None:
        for line in reversed(text.splitlines()):
            candidate = line.strip()
            if not candidate.startswith("{"):
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    @staticmethod
    def _percent_from_fraction(bucket: dict[str, Any] | None) -> int | None:
        if not bucket:
            return None
        fraction = bucket.get("remainingFraction")
        if fraction is None:
            return None
        try:
            value = float(fraction)
        except (TypeError, ValueError):
            return None
        return max(0, min(100, int(round(value * 100))))

    @staticmethod
    def _reset_from_iso(value: str | None) -> str | None:
        if not value:
            return None
        try:
            target = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        now = datetime.now(target.tzinfo or ZoneInfo("UTC"))
        delta = max(0, int((target - now).total_seconds()))
        hours, remainder = divmod(delta, 3600)
        minutes = remainder // 60
        if hours and minutes:
            return f"resets in {hours}h {minutes}m"
        if hours:
            return f"resets in {hours}h"
        return f"resets in {minutes}m"

    @staticmethod
    def _read_gemini_account_email() -> str | None:
        path = Path.home() / ".gemini" / "google_accounts.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        active = payload.get("active")
        return active if isinstance(active, str) and "@" in active else None

    @staticmethod
    def _is_waiting_for_authentication(raw_text: str) -> bool:
        clean = strip_ansi(raw_text).lower()
        return "waiting for authentication" in clean

    def close(self) -> None:
        self.session.close()


class CopilotProvider:
    """Fetch premium-request usage from the local GitHub Copilot CLI."""

    def __init__(self, cwd: str) -> None:
        binary = shutil.which("copilot")
        if not binary:
            raise FileNotFoundError("copilot not found on PATH")
        self.session = PersistentPTYSession(binary=binary, args=[], cwd=cwd)

    def fetch(self) -> CopilotStatus:
        last_error: Exception | None = None
        last_raw = ""
        for attempt in range(2):
            warmup_raw = self.session.capture(
                "",
                CaptureConfig(
                    timeout=20.0,
                    startup_wait=1.0 if attempt == 0 else 1.6,
                    idle_timeout=2.5,
                    discard_preexisting_output=False,
                    stop_substrings=("Environment loaded:", "Type your message"),
                    settle_after_stop=1.0,
                ),
            )
            raw = self.session.capture(
                "",
                CaptureConfig(
                    timeout=8.0,
                    startup_wait=0.4,
                    idle_timeout=1.8,
                    discard_preexisting_output=False,
                    stop_substrings=("Requests", "Premium"),
                    settle_after_stop=0.8,
                ),
            )
            merged = f"{warmup_raw}\n{raw}"
            if _is_empty_or_echo(merged, ""):
                last_error = ValueError("empty Copilot output")
                last_raw = merged
                self.session.close()
                continue
            try:
                status = parse_copilot_status(merged)
                status.premium_reset = self._monthly_reset_label()
                return status
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                last_raw = merged
                if (
                    "empty" in str(exc).lower()
                    or "could not parse copilot premium requests" in str(exc).lower()
                ):
                    self.session.close()
                    continue
                raise ProbeFailure(str(exc), strip_ansi(merged)) from exc
        assert last_error is not None
        raise ProbeFailure(str(last_error), strip_ansi(last_raw)) from last_error

    def close(self) -> None:
        self.session.close()

    @staticmethod
    def _monthly_reset_label() -> str:
        now = datetime.now(timezone.utc)
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        reset = datetime(year, month, 1, 0, 0, tzinfo=timezone.utc)
        return f"Resets {reset.strftime('%b %d %I:%M %p UTC')}"


def _read_safari_cookies(host_filter: str) -> dict[str, str]:
    """Parse Safari's Cookies.binarycookies file, return cookies matching host_filter.

    Returns a dict of {cookie_name: cookie_value} for cookies whose URL
    contains host_filter (case-insensitive).
    """
    import struct

    cookie_file = (
        Path.home()
        / "Library"
        / "Containers"
        / "com.apple.Safari"
        / "Data"
        / "Library"
        / "Cookies"
        / "Cookies.binarycookies"
    )
    if not cookie_file.exists():
        return {}

    try:
        data = cookie_file.read_bytes()
    except OSError:
        return {}

    if len(data) < 8 or data[:4] != b"cook":
        return {}

    num_pages = struct.unpack(">I", data[4:8])[0]
    page_sizes: list[int] = []
    offset = 8
    for _ in range(num_pages):
        if offset + 4 > len(data):
            return {}
        page_sizes.append(struct.unpack(">I", data[offset : offset + 4])[0])
        offset += 4

    cookies: dict[str, str] = {}

    for page_size in page_sizes:
        page_data = data[offset : offset + page_size]
        offset += page_size
        if len(page_data) < 8:
            continue
        cookie_count = struct.unpack("<I", page_data[4:8])[0]
        cookie_offsets: list[int] = []
        co = 8
        for _ in range(cookie_count):
            if co + 4 > len(page_data):
                break
            cookie_offsets.append(struct.unpack("<I", page_data[co : co + 4])[0])
            co += 4

        for c_off in cookie_offsets:
            if c_off + 48 > len(page_data):
                continue
            cookie_data = page_data[c_off:]
            if len(cookie_data) < 48:
                continue

            def _read_cstr(d: bytes, o: int) -> str:
                end = d.index(b"\x00", o) if b"\x00" in d[o:] else len(d)
                return d[o:end].decode("utf-8", errors="replace")

            try:
                url_off = struct.unpack("<I", cookie_data[16:20])[0]
                name_off = struct.unpack("<I", cookie_data[20:24])[0]
                value_off = struct.unpack("<I", cookie_data[28:32])[0]
                url = _read_cstr(cookie_data, url_off)
                name = _read_cstr(cookie_data, name_off)
                value = _read_cstr(cookie_data, value_off)
            except (ValueError, IndexError):
                continue

            if host_filter.lower() in url.lower():
                cookies[name] = value

    return cookies


class VibeProvider:
    """Fetch usage from the Mistral Vibe console API using browser cookies."""

    COOKIE_FILENAME = ".mistral_cookies.json"
    API_URL = "https://console.mistral.ai/api/billing/v2/vibe-usage"

    def __init__(self, project_root: str) -> None:
        self._project_root = project_root
        self._ory_name = ""
        self._ory_value = ""
        self._csrf = ""
        self._browser_opened = False
        self._load_cookies()

    def _load_cookies(self) -> None:
        """Try all cookie sources. Opens browser on first failure."""
        cookies = self._extract_safari_cookies() or self._extract_chrome_cookies()
        if cookies is None:
            cookie_path = Path(self._project_root) / self.COOKIE_FILENAME
            if cookie_path.exists():
                cookies = self._load_cookie_file(cookie_path)
        if cookies:
            self._ory_name = cookies["ory_session_name"]
            self._ory_value = cookies["ory_session_value"]
            self._csrf = cookies["csrftoken"]
        elif not self._browser_opened:
            # Open console.mistral.ai so the user can log in
            try:
                subprocess.Popen(
                    ["open", "https://console.mistral.ai"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass
            self._browser_opened = True

    @property
    def _has_cookies(self) -> bool:
        return bool(self._ory_name and self._ory_value and self._csrf)

    @staticmethod
    def _load_cookie_file(cookie_path: Path) -> dict[str, str]:
        """Load cookies from a manual JSON file."""
        try:
            data = json.loads(cookie_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(
                f"Failed to read Mistral cookies from {cookie_path}: {exc}"
            ) from exc
        ory_name = data.get("ory_session_name", "")
        ory_value = data.get("ory_session_value", "")
        csrf = data.get("csrftoken", "")
        if not ory_name or not ory_value or not csrf:
            raise ValueError(
                f"Mistral cookie file {cookie_path} must contain "
                '"ory_session_name", "ory_session_value", and "csrftoken" keys '
                "with non-empty values."
            )
        return {
            "ory_session_name": ory_name,
            "ory_session_value": ory_value,
            "csrftoken": csrf,
        }

    @staticmethod
    def _extract_chrome_cookies() -> dict[str, str] | None:
        """Try to extract Mistral cookies from Chrome's encrypted cookie store."""
        import hashlib
        import sqlite3 as _sqlite3

        chrome_cookies = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "Default"
            / "Cookies"
        )
        if not chrome_cookies.exists():
            return None

        # Get Chrome's encryption key from macOS Keychain
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    "Chrome Safe Storage",
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            chrome_password = result.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return None

        # Derive AES key via PBKDF2
        key = hashlib.pbkdf2_hmac(
            "sha1",
            chrome_password.encode("utf-8"),
            b"saltysalt",
            1003,
            dklen=16,
        )

        # Query Chrome's cookie database for mistral.ai cookies
        try:
            conn = _sqlite3.connect(f"file:{chrome_cookies}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT name, encrypted_value FROM cookies "
                    "WHERE host_key LIKE '%mistral.ai%' "
                    "AND (name LIKE 'ory_session_%' OR name = 'csrftoken')"
                ).fetchall()
            finally:
                conn.close()
        except _sqlite3.Error:
            return None

        if not rows:
            return None

        # Decrypt each cookie value
        ory_name = ""
        ory_value = ""
        csrf = ""
        for name, encrypted_value in rows:
            if not encrypted_value:
                continue
            decrypted = VibeProvider._decrypt_chrome_cookie(key, encrypted_value)
            if decrypted is None:
                continue
            if name.startswith("ory_session_"):
                ory_name = name
                ory_value = decrypted
            elif name == "csrftoken":
                csrf = decrypted

        if ory_name and ory_value and csrf:
            log.debug("Extracted Mistral cookies from Chrome automatically")
            return {
                "ory_session_name": ory_name,
                "ory_session_value": ory_value,
                "csrftoken": csrf,
            }
        return None

    @staticmethod
    def _extract_safari_cookies() -> dict[str, str] | None:
        """Try to extract Mistral cookies from Safari's binarycookies store."""
        cookies = _read_safari_cookies("mistral")
        ory_name = next((k for k in cookies if k.startswith("ory_session_")), "")
        ory_value = cookies.get(ory_name, "")
        csrf = cookies.get("csrftoken", "")
        if ory_name and ory_value and csrf:
            log.debug("Extracted Mistral cookies from Safari automatically")
            return {
                "ory_session_name": ory_name,
                "ory_session_value": ory_value,
                "csrftoken": csrf,
            }
        return None

    @staticmethod
    def _decrypt_chrome_cookie(key: bytes, encrypted_value: bytes) -> str | None:
        """Decrypt a Chrome v10-encrypted cookie value using openssl."""
        # v10 prefix = macOS AES-128-CBC encryption
        if len(encrypted_value) < 4 or encrypted_value[:3] != b"v10":
            # Unencrypted or unknown format
            try:
                return encrypted_value.decode("utf-8")
            except UnicodeDecodeError:
                return None

        ciphertext = encrypted_value[3:]
        iv_hex = "20" * 16  # 16 space bytes as IV
        key_hex = key.hex()

        try:
            result = subprocess.run(
                [
                    "openssl",
                    "enc",
                    "-aes-128-cbc",
                    "-d",
                    "-K",
                    key_hex,
                    "-iv",
                    iv_hex,
                ],
                input=ciphertext,
                capture_output=True,
                timeout=5,
                check=True,
            )
            return result.stdout.decode("utf-8")
        except (subprocess.SubprocessError, OSError, UnicodeDecodeError):
            return None

    def fetch(self) -> VibeStatus:
        import urllib.error
        import urllib.request

        if not self._has_cookies:
            self._load_cookies()
        if not self._has_cookies:
            raise ProbeFailure(
                "Waiting for Mistral login — console.mistral.ai opened in browser",
                "",
            )

        cookie_header = f"{self._ory_name}={self._ory_value}; csrftoken={self._csrf}"
        req = urllib.request.Request(
            self.API_URL,
            headers={
                "Cookie": cookie_header,
                "x-csrftoken": self._csrf,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 401, 403):
                # Session expired — clear cookies so next fetch retries extraction
                self._ory_name = self._ory_value = self._csrf = ""
                raise ProbeFailure(
                    "Mistral session expired. Log into console.mistral.ai to refresh.",
                    f"HTTP {exc.code}",
                ) from exc
            raise ProbeFailure(
                f"Mistral API returned HTTP {exc.code}", str(exc)
            ) from exc
        except urllib.error.URLError as exc:
            raise ProbeFailure(
                f"Could not reach Mistral API: {exc.reason}", str(exc)
            ) from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProbeFailure("Mistral API returned invalid JSON", body[:500]) from exc

        usage_pct_raw = payload.get("usage_percentage")
        usage_percent = (
            round(float(usage_pct_raw) * 100, 4) if usage_pct_raw is not None else None
        )
        reset_at = payload.get("reset_at")
        if reset_at:
            try:
                target = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
                reset_at = f"Resets {target.strftime('%b %d %I:%M %p UTC')}"
            except ValueError:
                pass

        return VibeStatus(
            usage_percent=usage_percent,
            reset_at=reset_at,
            payg_enabled=payload.get("payg_enabled"),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            raw_text=body,
        )

    def close(self) -> None:
        pass


class CursorProvider:
    """Fetch credit usage from Cursor's API."""

    _DB_PATH = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "globalStorage"
        / "state.vscdb"
    )
    _USAGE_URL = (
        "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
    )
    _PLAN_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetPlanInfo"
    _TOKEN_URL = "https://api2.cursor.sh/oauth/token"
    _CLIENT_ID = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._browser_opened = False
        self._load_token()

    def _load_token(self) -> None:
        """Try all token sources. Opens browser on first failure."""
        # Try Safari cookie first (WorkosCursorSessionToken = userId::jwt)
        token = self._extract_token_from_safari()
        if token:
            self._access_token = token
            return

        # Fall back to Cursor Desktop's local SQLite database
        self._load_from_desktop_db()
        if self._access_token:
            return

        # No token found — open browser so user can log in
        if not self._browser_opened:
            try:
                subprocess.Popen(
                    ["open", "https://cursor.com/settings"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass
            self._browser_opened = True

    def _extract_token_from_safari(self) -> str | None:
        """Extract access token from Safari's WorkosCursorSessionToken cookie."""
        import urllib.parse

        cookies = _read_safari_cookies("cursor")
        token_value = cookies.get("WorkosCursorSessionToken", "")
        if token_value:
            decoded = urllib.parse.unquote(token_value)
            parts = decoded.split("::", 1)
            if len(parts) == 2 and parts[1]:
                log.debug("Extracted Cursor token from Safari cookie")
                return parts[1]
        return None

    def _load_from_desktop_db(self) -> None:
        """Load tokens from Cursor Desktop's local SQLite database."""
        import sqlite3 as _sqlite3

        if not self._DB_PATH.exists():
            return
        try:
            conn = _sqlite3.connect(f"file:{self._DB_PATH}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT value FROM cursorDiskKV "
                    "WHERE key = 'cursorAuth/accessToken'"
                ).fetchone()
                self._access_token = row[0] if row else None
                row = conn.execute(
                    "SELECT value FROM cursorDiskKV "
                    "WHERE key = 'cursorAuth/refreshToken'"
                ).fetchone()
                self._refresh_token = row[0] if row else None
            finally:
                conn.close()
        except _sqlite3.Error:
            return

    def fetch(self) -> CursorStatus:
        """Fetch usage and plan data from Cursor's API."""
        import urllib.error as _ue
        import urllib.request as _ur

        if not self._access_token:
            self._load_token()
        if not self._access_token:
            raise ProbeFailure(
                "Waiting for Cursor login — cursor.com/settings opened in browser",
                "",
            )

        usage_data: dict[str, Any] = {}
        plan_data: dict[str, Any] = {}

        try:
            usage_data = self._api_post(_ur, _ue, self._USAGE_URL)
        except _ue.HTTPError as exc:
            if exc.code == 401 and self._refresh_token:
                self._do_token_refresh(_ur, _ue)
                usage_data = self._api_post(_ur, _ue, self._USAGE_URL)
            elif exc.code == 401:
                # Token expired, no refresh token — clear so next fetch retries
                self._access_token = None
                raise ProbeFailure(
                    "Cursor session expired. Log into cursor.com to refresh.",
                    f"HTTP {exc.code}",
                ) from exc
            else:
                raise ProbeFailure(f"Cursor API error: HTTP {exc.code}", "") from exc
        except (OSError, _ue.URLError) as exc:
            raise ProbeFailure(f"Cursor API network error: {exc}", "") from exc

        try:
            plan_data = self._api_post(_ur, _ue, self._PLAN_URL)
        except Exception:  # noqa: BLE001
            pass  # plan info is optional

        plan_usage = usage_data.get("planUsage") or {}
        credit_percent_left: float | None = None

        # Try totalPercentUsed first, fall back to remaining/limit cents
        total_percent_used = usage_data.get("totalPercentUsed")
        if total_percent_used is not None:
            try:
                credit_percent_left = round(100.0 - float(total_percent_used), 2)
            except (TypeError, ValueError):
                pass
        if credit_percent_left is None:
            raw_remaining = plan_usage.get("remaining")
            raw_limit = plan_usage.get("limit")
            if raw_remaining is not None and raw_limit is not None:
                try:
                    remaining = int(raw_remaining)
                    limit = int(raw_limit)
                    if limit > 0:
                        credit_percent_left = round((remaining / limit) * 100.0, 2)
                except (TypeError, ValueError):
                    pass

        auto_percent_used: float | None = None
        raw_auto = usage_data.get("autoPercentUsed")
        if raw_auto is not None:
            try:
                auto_percent_used = float(raw_auto)
            except (TypeError, ValueError):
                pass

        api_percent_used: float | None = None
        raw_api = usage_data.get("apiPercentUsed")
        if raw_api is not None:
            try:
                api_percent_used = float(raw_api)
            except (TypeError, ValueError):
                pass

        remaining_cents: int | None = None
        raw_remaining = plan_usage.get("remaining")
        if raw_remaining is not None:
            try:
                remaining_cents = int(raw_remaining)
            except (TypeError, ValueError):
                pass

        limit_cents: int | None = None
        raw_limit = plan_usage.get("limit")
        if raw_limit is not None:
            try:
                limit_cents = int(raw_limit)
            except (TypeError, ValueError):
                pass

        billing_cycle_start: str | None = None
        raw_start = usage_data.get("billingCycleStart") or plan_data.get(
            "billingCycleStart"
        )
        if raw_start is not None:
            try:
                ms = int(raw_start)
                start_dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                billing_cycle_start = start_dt.date().isoformat()
            except (TypeError, ValueError):
                pass

        billing_cycle_end: str | None = None
        billing_cycle_end_iso: str | None = None
        raw_end = usage_data.get("billingCycleEnd") or plan_data.get("billingCycleEnd")
        if raw_end is not None:
            try:
                ms = int(raw_end)
                target = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
                billing_cycle_end = f"Resets {target.strftime('%b %d %I:%M %p UTC')}"
                billing_cycle_end_iso = target.date().isoformat()
            except (TypeError, ValueError):
                pass

        plan_name = plan_data.get("planName")

        raw_text = json.dumps(
            {"usage": usage_data, "plan": plan_data},
            indent=2,
            sort_keys=True,
        )
        return CursorStatus(
            credit_percent_left=credit_percent_left,
            auto_percent_used=auto_percent_used,
            api_percent_used=api_percent_used,
            remaining_cents=remaining_cents,
            limit_cents=limit_cents,
            plan_name=(plan_name if isinstance(plan_name, str) else None),
            billing_cycle_start=billing_cycle_start,
            billing_cycle_end=billing_cycle_end,
            billing_cycle_end_iso=billing_cycle_end_iso,
            raw_text=raw_text,
        )

    def close(self) -> None:
        """No-op: no persistent session to clean up."""

    def _api_post(self, ur: Any, ue: Any, url: str) -> dict[str, Any]:
        """POST to a Cursor API endpoint, return parsed JSON."""
        req = ur.Request(
            url,
            data=b"{}",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
            },
            method="POST",
        )
        with ur.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = resp.read()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    def _do_token_refresh(self, ur: Any, ue: Any) -> None:
        """Refresh the access token using the refresh token."""
        payload = json.dumps(
            {
                "grant_type": "refresh_token",
                "client_id": self._CLIENT_ID,
                "refresh_token": self._refresh_token,
            }
        ).encode()
        req = ur.Request(
            self._TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with ur.urlopen(req, timeout=15) as resp:  # noqa: S310
                body = json.loads(resp.read())
            new_token = body.get("access_token")
            if new_token:
                self._access_token = new_token
                log.debug("Cursor access token refreshed successfully")
            else:
                log.warning("Cursor token refresh response missing access_token")
        except Exception as exc:  # noqa: BLE001
            log.warning("Cursor token refresh failed: %s", exc)


def fetch_provider_snapshot(
    name: str, fetcher: Any, debug: bool = False
) -> ProviderSnapshot:
    """Wrap provider fetch failures into a display-friendly snapshot."""

    try:
        status = fetcher.fetch()
    except ProbeFailure as exc:
        if debug:
            _write_debug_dump(name, exc.raw_text or "")
        message = str(exc)
        if debug:
            tail = exc.raw_text[-1600:] if exc.raw_text else ""
            dump_hint = f"raw dump: {_debug_dump_path(name)}"
            message = f"{message}\n\n{dump_hint}\n\n{tail}".strip()
        return ProviderSnapshot(name=name, ok=False, source="cli", error=message)
    except Exception as exc:  # noqa: BLE001
        return ProviderSnapshot(name=name, ok=False, source="cli", error=str(exc))
    data = status.to_dict()
    if debug:
        _write_debug_dump(name, str(data.get("raw_text", "")))
    if not debug:
        data.pop("raw_text", None)
    return ProviderSnapshot(name=name, ok=True, source="cli", data=data)
