"""Provider probes for Codex and Claude usage."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from typing import Any

from .parsing import ClaudeStatus, CodexStatus, parse_claude_status, parse_codex_status, strip_ansi
from .pty_session import CaptureConfig, PersistentPTYSession


TRUST_PROMPTS = (
    ("Do you trust the files in this folder?", "y\r"),
    ("Quick safety check:", "\r"),
    ("Yes, I trust this folder", "\r"),
    ("Ready to code here?", "\r"),
    ("Press Enter to continue", "\r"),
)


@dataclass(slots=True)
class ProviderSnapshot:
    name: str
    ok: bool
    source: str
    data: dict[str, Any] | None = None
    error: str | None = None


class ProbeFailure(RuntimeError):
    """Raised when a provider captured output but parsing still failed."""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


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
        for attempt in range(2):
            raw = self.session.capture(
                "/status",
                CaptureConfig(
                    timeout=12.0,
                    startup_wait=0.6 if attempt == 0 else 1.0,
                    idle_timeout=2.5,
                    stop_substrings=("Credits:", "5h limit", "5-hour limit", "Weekly limit"),
                    settle_after_stop=1.5,
                    send_enter_every=1.2,
                    resend_command_every=3.0,
                    resend_command_max=2,
                ),
            )
            try:
                return parse_codex_status(raw)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                last_raw = raw
                if "empty" in str(exc).lower() or "data not available yet" in str(exc).lower():
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
                    ),
                    settle_after_stop=2.0,
                    send_enter_every=0.8,
                    resend_command_every=5.0,
                    resend_command_max=1,
                    auto_responses=TRUST_PROMPTS + (("Show plan usage limits", "\r"), ("Show plan", "\r")),
                ),
            )
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
                last_combined = f"--- usage ---\n{strip_ansi(usage_raw)}\n--- status ---\n{strip_ansi(status_raw)}"
                message = str(exc).lower()
                if "empty" in message or "missing current session" in message:
                    self.session.close()
                    continue
                raise ProbeFailure(str(exc), last_combined) from exc
        assert last_error is not None
        raise ProbeFailure(str(last_error), last_combined) from last_error

    def close(self) -> None:
        self.session.close()


def fetch_provider_snapshot(name: str, fetcher: Any, debug: bool = False) -> ProviderSnapshot:
    """Wrap provider fetch failures into a display-friendly snapshot."""

    try:
        status = fetcher.fetch()
    except ProbeFailure as exc:
        message = str(exc)
        if debug:
            tail = exc.raw_text[-1600:] if exc.raw_text else ""
            message = f"{message}\n\n{tail}".strip()
        return ProviderSnapshot(name=name, ok=False, source="cli", error=message)
    except Exception as exc:  # noqa: BLE001
        return ProviderSnapshot(name=name, ok=False, source="cli", error=str(exc))
    data = status.to_dict()
    if not debug:
        data.pop("raw_text", None)
    return ProviderSnapshot(name=name, ok=True, source="cli", data=data)
