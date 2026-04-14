"""Provider probes for Codex, Claude, Gemini, and Copilot usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
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
    GeminiStatus,
    parse_claude_status,
    parse_copilot_status,
    parse_codex_status,
    parse_gemini_status,
    strip_ansi,
)
from .pty_session import CaptureConfig, PersistentPTYSession


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
                    stop_substrings=("Credits:", "5h limit", "5-hour limit", "Weekly limit"),
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
                    auto_responses=TRUST_PROMPTS + (("Show plan usage limits", "\r"), ("Show plan", "\r")),
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
                    auto_responses=TRUST_PROMPTS + (("Show Claude Code status", "\r"), ("Show Claude Code", "\r")),
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
                    stop_substrings=("Session Stats", "Usage remaining", "gemini-2.5-pro", "gemini-3.1-pro-preview"),
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
                if "empty" in str(exc).lower() or "could not find gemini session stats panel" in str(exc).lower():
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

    def _fetch_via_bundle_quota_probe(self, node: str, module_root: Path) -> GeminiStatus | None:
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

    def _fetch_via_legacy_dist_probe(self, node: str, module_root: Path) -> GeminiStatus | None:
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

    def _gemini_status_from_payload(self, payload: dict[str, Any]) -> GeminiStatus | None:
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
            flash_reset=self._reset_from_iso(flash_bucket["resetTime"]) if flash_bucket else None,
            pro_reset=self._reset_from_iso(pro_bucket["resetTime"]) if pro_bucket else None,
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
            if "makeFakeConfig" in text and "refreshUserQuota" in text and "AuthType" in text:
                return candidate
        return None

    @staticmethod
    def _gemini_module_root_from_binary(binary: str) -> Path | None:
        resolved = Path(binary).resolve()
        candidates = (
            resolved.parent.parent if resolved.name == "gemini.js" and resolved.parent.name == "bundle" else None,
            resolved.parent.parent if resolved.name == "index.js" and resolved.parent.name == "dist" else None,
            resolved.parent.parent / "libexec" / "lib" / "node_modules" / "@google" / "gemini-cli",
            resolved.parent.parent.parent / "libexec" / "lib" / "node_modules" / "@google" / "gemini-cli",
        )
        for candidate in candidates:
            if candidate is not None and candidate.exists():
                return candidate
        return None

    def _gemini_module_root(self) -> Path | None:
        return self._gemini_module_root_from_binary(self.binary)

    @staticmethod
    def _find_bucket(buckets: list[dict[str, Any]], *model_ids: str) -> dict[str, Any] | None:
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
                return parse_copilot_status(merged)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                last_raw = merged
                if "empty" in str(exc).lower() or "could not parse copilot premium requests" in str(exc).lower():
                    self.session.close()
                    continue
                raise ProbeFailure(str(exc), strip_ansi(merged)) from exc
        assert last_error is not None
        raise ProbeFailure(str(last_error), strip_ansi(last_raw)) from last_error

    def close(self) -> None:
        self.session.close()


def fetch_provider_snapshot(name: str, fetcher: Any, debug: bool = False) -> ProviderSnapshot:
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
