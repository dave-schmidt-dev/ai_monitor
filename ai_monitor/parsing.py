"""Parsing helpers for Codex, Claude, Gemini, and Copilot usage output."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\))")
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
NUMBER_RE = re.compile(r"([0-9][0-9.,]*)")
PERCENT_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%")
RESET_RE = re.compile(r"(Resets?[^\r\n]*)", re.IGNORECASE)
BLOCK_CHARS_RE = re.compile(r"[█▉▊▋▌▍▎▏▓▒░]+")


def strip_ansi(text: str) -> str:
    """Remove ANSI escapes and low ASCII control bytes from terminal captures."""

    text = ANSI_RE.sub("", text)
    text = CTRL_RE.sub("", text)
    return text.replace("\r", "\n")


def compact_whitespace(text: str) -> str:
    """Reduce consecutive blank lines in PTY captures."""

    lines = [line.rstrip() for line in text.splitlines()]
    output: list[str] = []
    blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and blank:
            continue
        output.append(line)
        blank = is_blank
    return "\n".join(output).strip()


def parse_number(text: str | None) -> float | None:
    """Parse a decimal-like number that may include separators."""

    if not text:
        return None
    match = NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def percent_from_line(line: str, assume_remaining_when_unclear: bool = False) -> int | None:
    """Extract a remaining-percent value from a rendered usage line."""

    if "|" in line and any(model in line.lower() for model in ("opus", "sonnet", "haiku", "default")):
        return None
    match = PERCENT_RE.search(line)
    if not match:
        return None
    raw = max(0.0, min(100.0, float(match.group(1))))
    lower = line.lower()
    if any(word in lower for word in ("used", "spent", "consumed")):
        return int(round(100 - raw))
    if any(word in lower for word in ("left", "remaining", "available")):
        return int(round(raw))
    if assume_remaining_when_unclear:
        return int(round(raw))
    return None


def extract_reset_from_line(line: str) -> str | None:
    """Return a normalized reset string when present."""

    match = RESET_RE.search(line)
    if not match:
        return None
    value = match.group(1).strip()
    value = re.split(r"(?i)\b(?:Weekly limit|5h limit|Current session|Currentweek|Current week)\b", value, maxsplit=1)[0]
    value = BLOCK_CHARS_RE.sub("", value)
    value = re.sub(r"[│\[\]]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(" )")
    value = re.sub(r"(?i)\bResets(?=[A-Z])", "Resets ", value)
    value = re.sub(r"(?i)\bResets(?=[A-Za-z]{3}\d)", "Resets ", value)
    value = re.sub(r"(?i)\bat(?=\d)", "at ", value)
    value = re.sub(r"(?i)(\d)at(\d)", r"\1 at \2", value)
    value = re.sub(r"(?i)(\d)(am|pm)\b", r"\1 \2", value)
    value = re.sub(r"(?i)(\d)([A-Z][a-z]{2}\b)", r"\1 \2", value)
    value = re.sub(r"(?i)\b([A-Z][a-z]{2})(\d)", r"\1 \2", value)
    if value.count("(") > value.count(")"):
        value += ")"
    value = _shorten_timezone_labels(value)
    return value


def _shorten_timezone_labels(value: str) -> str:
    """Replace verbose timezone names with short labels for compact display."""

    eastern = datetime.now(ZoneInfo("America/New_York")).tzname() or "ET"
    return value.replace("(America/New_York)", f"({eastern})")


@dataclass(slots=True)
class CodexStatus:
    credits: float | None
    five_hour_percent_left: int | None
    weekly_percent_left: int | None
    five_hour_reset: str | None
    weekly_reset: str | None
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ClaudeStatus:
    session_percent_left: int | None
    weekly_percent_left: int | None
    opus_percent_left: int | None
    primary_reset: str | None
    secondary_reset: str | None
    opus_reset: str | None
    account_email: str | None
    account_organization: str | None
    login_method: str | None
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GeminiStatus:
    flash_percent_left: int | None
    pro_percent_left: int | None
    flash_reset: str | None
    pro_reset: str | None
    account_email: str | None
    account_tier: str | None
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CopilotStatus:
    premium_requests: int | None
    sample_duration_seconds: int | None
    premium_percent_left: int | None
    premium_reset: str | None
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_codex_status(text: str) -> CodexStatus:
    """Parse Codex `/status` output after PTY capture."""

    clean = compact_whitespace(strip_ansi(text))
    if not clean:
        raise ValueError("empty Codex output")
    credits_match = re.search(r"Credits:\s*([0-9][0-9.,]*)", clean, re.IGNORECASE)
    credits = parse_number(credits_match.group(1) if credits_match else None)

    lines = clean.splitlines()
    five_idx = next((idx for idx, line in enumerate(lines) if "5h limit" in line.lower()), None)
    week_idx = next((idx for idx, line in enumerate(lines) if "weekly limit" in line.lower()), None)

    def combined_line(start_idx: int | None) -> str | None:
        if start_idx is None:
            return None
        window = [part.strip() for part in lines[start_idx : start_idx + 2] if part.strip()]
        return " ".join(window) if window else None

    five_line = combined_line(five_idx)
    week_line = combined_line(week_idx)

    five_pct = percent_from_line(five_line or "", assume_remaining_when_unclear=False)
    week_pct = percent_from_line(week_line or "", assume_remaining_when_unclear=False)
    five_reset = extract_reset_from_line(five_line or "")
    week_reset = extract_reset_from_line(week_line or "")

    if credits is None and five_pct is None and week_pct is None:
        if "data not available yet" in clean.lower():
            raise ValueError("Codex status data not available yet")
        if "update available" in clean.lower() and "codex" in clean.lower():
            raise ValueError("Codex CLI update required before probing usage")
        raise ValueError("could not parse Codex status")

    return CodexStatus(
        credits=credits,
        five_hour_percent_left=five_pct,
        weekly_percent_left=week_pct,
        five_hour_reset=five_reset,
        weekly_reset=week_reset,
        raw_text=clean,
    )


def _normalize_label(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _extract_identity(text: str) -> tuple[str | None, str | None, str | None]:
    email = None
    for pattern in (
        r"(?i)Account:\s+([^\s@]+@[^\s@]+)",
        r"(?i)Email:\s+([^\s@]+@[^\s@]+)",
        r"(?i)([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    ):
        match = re.search(pattern, text)
        if match:
            email = match.group(1).strip()
            break

    organization = None
    for pattern in (r"(?i)Org:\s*(.+)", r"(?i)Organization:\s*(.+)"):
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            if candidate and candidate.lower() != (email or "").lower():
                organization = candidate
                break

    login = None
    explicit = re.search(r"(?i)login\s+method:\s*(.+)", text)
    if explicit:
        login = explicit.group(1).strip()
    else:
        plan = re.search(r"(?i)(claude\s+[a-z0-9][a-z0-9\s._-]{0,24})", text)
        if plan:
            login = plan.group(1).strip()

    return email, organization, login


def _trim_to_latest_usage_panel(text: str) -> str:
    marker_index = text.lower().rfind("settings:")
    if marker_index == -1:
        return text
    tail = text[marker_index:]
    lower = tail.lower()
    if "usage" not in lower:
        return text
    if "%" not in tail and "loading usage" not in lower and "failed to load usage data" not in lower:
        return text
    return tail


def _extract_percent_for_label(lines: list[str], label: str) -> int | None:
    normalized_label = _normalize_label(label)
    normalized_lines = [_normalize_label(line) for line in lines]
    for idx, normalized in enumerate(normalized_lines):
        if normalized_label not in normalized:
            continue
        for candidate in lines[idx : idx + 12]:
            pct = percent_from_line(candidate)
            if pct is not None:
                return pct
    return None


def _extract_percent_after_index(lines: list[str], start_idx: int) -> int | None:
    for candidate in lines[start_idx : start_idx + 12]:
        pct = percent_from_line(candidate, assume_remaining_when_unclear=True)
        if pct is not None:
            return pct
    return None


def _extract_reset_for_label(lines: list[str], label: str) -> str | None:
    normalized_label = _normalize_label(label)
    normalized_lines = [_normalize_label(line) for line in lines]
    for idx, normalized in enumerate(normalized_lines):
        if normalized_label not in normalized:
            continue
        for candidate in lines[idx : idx + 14]:
            candidate_normalized = _normalize_label(candidate)
            if candidate_normalized.startswith("current") and normalized_label not in candidate_normalized:
                break
            reset = extract_reset_from_line(candidate)
            if reset:
                return reset
    return None


def _find_line_index(lines: list[str], *labels: str) -> int | None:
    normalized_lines = [_normalize_label(line) for line in lines]
    normalized_labels = [_normalize_label(label) for label in labels]
    for idx, line in enumerate(normalized_lines):
        if any(label in line for label in normalized_labels):
            return idx
    return None


def _normalize_compact_reset(value: str) -> str:
    reset = extract_reset_from_line(f"Resets {value.strip()}")
    if reset:
        return reset
    compact = re.sub(r"\s+", " ", value).strip()
    compact = re.sub(r"(?i)(\d)(am|pm)\b", r"\1 \2", compact)
    compact = _shorten_timezone_labels(compact)
    return f"Resets {compact}".strip()


def _extract_compact_claude_rows(panel: str) -> dict[str, tuple[int | None, str | None]]:
    label_re = re.compile(
        r"Current session|Current week \(all models\)|Current week \(Opus\)|Current week \(Sonnet only\)|Current week \(Sonnet\)",
        re.IGNORECASE,
    )
    squashed = re.sub(r"\s+", " ", panel).strip()
    matches = list(label_re.finditer(squashed))
    if not matches:
        return {}

    rows: dict[str, tuple[int | None, str | None]] = {}
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(squashed)
        segment = squashed[match.start() : end]
        label = match.group(0).lower()
        percent_left = None
        percent_match = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%\s*used", segment, re.IGNORECASE)
        if percent_match:
            percent_left = max(0, min(100, int(round(100 - float(percent_match.group(1))))))
        else:
            left_match = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%\s*(?:left|remaining|available)", segment, re.IGNORECASE)
            if left_match:
                percent_left = max(0, min(100, int(round(float(left_match.group(1))))))

        reset = None
        reset_match = re.search(
            r"Resets?\s*(.+?)\s*[0-9]{1,3}(?:\.[0-9]+)?\s*%\s*(?:used|left|remaining|available)",
            segment,
            re.IGNORECASE,
        )
        if reset_match:
            reset = _normalize_compact_reset(reset_match.group(1))
        rows[label] = (percent_left, reset)
    return rows


def _is_overcaptured_reset(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return "current week" in lowered or "%used" in lowered or "extra usage" in lowered


def _extract_usage_error(text: str) -> str | None:
    lower = text.lower()
    compact = "".join(lower.split())
    if "do you trust the files in this folder?" in lower and "current session" not in lower:
        return "Claude CLI is waiting for a folder trust prompt"
    if "/usage is only available for subscription plans" in lower or "/usageisonlyvilableforsubscriptionplans" in compact:
        return "Claude CLI says /usage is only available for subscription plans"
    if "rate limited" in lower or "ratelimited" in compact:
        return "Claude CLI usage endpoint is rate limited right now"
    if "failed to load usage data" in lower or "failedtoloadusagedata" in compact:
        return "Claude CLI could not load usage data"
    match = re.search(r"Failed\s*to\s*load\s*usage\s*data:\s*(\{.*\})", text, re.IGNORECASE | re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(1).replace("\n", "").replace("\r", ""))
        except json.JSONDecodeError:
            return None
        error = payload.get("error", {})
        message = str(error.get("message", "")).strip()
        details = error.get("details", {}) or {}
        code = str(details.get("error_code", "")).strip()
        if str(error.get("type", "")).lower() == "rate_limit_error":
            return "Claude CLI usage endpoint is rate limited right now"
        if code.lower().find("token") >= 0:
            return f"Claude CLI error: {message} ({code}). Run `claude login` to refresh.".strip()
        return f"Claude CLI error: {message} ({code})".strip()
    return None


def parse_claude_status(usage_text: str, status_text: str | None = None) -> ClaudeStatus:
    """Parse Claude `/usage` output after PTY capture."""

    usage_clean = compact_whitespace(strip_ansi(usage_text))
    status_clean = compact_whitespace(strip_ansi(status_text or ""))
    if not usage_clean:
        raise ValueError("empty Claude output")

    usage_error = _extract_usage_error(usage_clean)
    if usage_error:
        raise ValueError(usage_error)

    panel = _trim_to_latest_usage_panel(usage_clean)
    lines = panel.splitlines()
    compact = "".join(panel.lower().split())
    has_weekly = "currentweek" in compact
    has_opus = "currentweek(opus)" in compact or "currentweek(sonnetonly)" in compact or "currentweek(sonnet)" in compact

    session = _extract_percent_for_label(lines, "Current session")
    weekly = _extract_percent_for_label(lines, "Current week (all models)")
    opus = (
        _extract_percent_for_label(lines, "Current week (Opus)")
        or _extract_percent_for_label(lines, "Current week (Sonnet only)")
        or _extract_percent_for_label(lines, "Current week (Sonnet)")
    )

    session_idx = _find_line_index(lines, "Current session")
    weekly_idx = _find_line_index(lines, "Current week (all models)")
    opus_idx = _find_line_index(lines, "Current week (Opus)", "Current week (Sonnet only)", "Current week (Sonnet)")

    if session is None and session_idx is not None:
        session = _extract_percent_after_index(lines, session_idx)
    if has_weekly and weekly is None and weekly_idx is not None:
        weekly = _extract_percent_after_index(lines, weekly_idx)
    if has_opus and opus is None and opus_idx is not None:
        opus = _extract_percent_after_index(lines, opus_idx)

    ordered = [pct for pct in (percent_from_line(line) for line in lines) if pct is not None]
    if session is None and ordered:
        session = ordered[0]
    if has_weekly and weekly is None and len(ordered) > 1:
        weekly = ordered[1]
    if has_opus and opus is None and len(ordered) > 2:
        opus = ordered[2]

    compact_rows = _extract_compact_claude_rows(panel)
    session_row = compact_rows.get("current session")
    weekly_row = compact_rows.get("current week (all models)")
    opus_row = (
        compact_rows.get("current week (opus)")
        or compact_rows.get("current week (sonnet only)")
        or compact_rows.get("current week (sonnet)")
    )
    if session is None and session_row:
        session = session_row[0]
    if has_weekly and weekly is None and weekly_row:
        weekly = weekly_row[0]
    if has_opus and opus is None and opus_row:
        opus = opus_row[0]
    if has_weekly and weekly_row and weekly_row[0] is not None and session_row and session_row[0] is not None:
        if weekly == session and weekly_row[0] != session_row[0]:
            weekly = weekly_row[0]
    if has_opus and opus_row and opus_row[0] is not None and session_row and session_row[0] is not None:
        if opus == session and opus_row[0] != session_row[0]:
            opus = opus_row[0]

    if session is None:
        raise ValueError("Missing Current session in Claude output")

    primary_reset = _extract_reset_for_label(lines, "Current session")
    secondary_reset = _extract_reset_for_label(lines, "Current week (all models)") if has_weekly else None
    opus_reset = (
        _extract_reset_for_label(lines, "Current week (Opus)")
        or _extract_reset_for_label(lines, "Current week (Sonnet only)")
        or _extract_reset_for_label(lines, "Current week (Sonnet)")
    ) if has_opus else None

    if (not primary_reset or _is_overcaptured_reset(primary_reset)) and session_row:
        primary_reset = session_row[1]
    if has_weekly and (not secondary_reset or _is_overcaptured_reset(secondary_reset)) and weekly_row:
        secondary_reset = weekly_row[1]
    if has_opus and (not opus_reset or _is_overcaptured_reset(opus_reset)) and opus_row:
        opus_reset = opus_row[1]

    account_email, account_organization, login_method = _extract_identity(f"{usage_clean}\n{status_clean}")

    return ClaudeStatus(
        session_percent_left=session,
        weekly_percent_left=weekly,
        opus_percent_left=opus,
        primary_reset=primary_reset,
        secondary_reset=secondary_reset,
        opus_reset=opus_reset,
        account_email=account_email,
        account_organization=account_organization,
        login_method=login_method,
        raw_text=f"{usage_clean}\n{status_clean}".strip(),
    )


def _normalize_gemini_line(line: str) -> str:
    line = re.sub(r"[│╭╮╰╯─▄]+", " ", line)
    return re.sub(r"\s+", " ", line).strip()


def _extract_gemini_quota_row(lines: list[str], model_markers: tuple[str, ...]) -> tuple[int | None, str | None]:
    for raw_line in lines:
        line = _normalize_gemini_line(raw_line)
        lower = line.lower()
        if not any(marker in lower for marker in model_markers):
            continue
        percent_match = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)%", line)
        reset_match = re.search(r"(resets?\s+in\s+\d+h(?:\s+\d+m)?)", line, re.IGNORECASE)
        percent = int(round(float(percent_match.group(1)))) if percent_match else None
        reset = reset_match.group(1) if reset_match else None
        return percent, reset
    return None, None


def parse_gemini_status(text: str) -> GeminiStatus:
    """Parse Gemini `/stats` output after PTY capture."""

    clean = compact_whitespace(strip_ansi(text))
    if not clean:
        raise ValueError("empty Gemini output")

    lines = [_normalize_gemini_line(line) for line in clean.splitlines() if _normalize_gemini_line(line)]
    flash_percent, flash_reset = _extract_gemini_quota_row(
        lines,
        ("gemini-2.5-flash ", "gemini-2.5-flash-lite", "gemini-3-flash-preview"),
    )
    pro_percent, pro_reset = _extract_gemini_quota_row(
        lines,
        ("gemini-2.5-pro", "gemini-3.1-pro-preview"),
    )

    account_email = None
    account_tier = None
    for line in lines:
        if line.startswith("Auth Method:"):
            email_match = re.search(r"\(([^()]+@[^()]+)\)", line)
            if email_match:
                account_email = email_match.group(1).strip()
        elif line.startswith("Tier:"):
            account_tier = line.split(":", 1)[1].strip() or None

    if flash_percent is None and pro_percent is None:
        if "session stats" not in clean.lower():
            raise ValueError("could not find Gemini session stats panel")
        raise ValueError("could not parse Gemini usage rows")

    return GeminiStatus(
        flash_percent_left=flash_percent,
        pro_percent_left=pro_percent,
        flash_reset=flash_reset,
        pro_reset=pro_reset,
        account_email=account_email,
        account_tier=account_tier,
        raw_text=clean,
    )


def _parse_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*([smh])\s*", value, re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    return amount * 3600


def parse_copilot_status(text: str) -> CopilotStatus:
    """Parse Copilot status-line usage text from interactive PTY capture."""

    clean = compact_whitespace(strip_ansi(text))
    if not clean:
        raise ValueError("empty Copilot output")

    request_matches = re.findall(r"Requests\s+(\d+)\s+Premium(?:\s+\(([^)]+)\))?", clean, re.IGNORECASE)
    remaining_matches = re.findall(r"Remaining\s+reqs?\.\s*:?\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*%", clean, re.IGNORECASE)
    if not request_matches and not remaining_matches:
        raise ValueError("could not parse Copilot premium requests")

    premium_requests = int(request_matches[-1][0]) if request_matches else None
    duration_text = request_matches[-1][1] if request_matches else None
    duration_seconds = _parse_duration_seconds(duration_text)
    premium_note = f"sample {duration_text.strip()}" if duration_text else None
    premium_percent_left = int(round(float(remaining_matches[-1]))) if remaining_matches else None

    return CopilotStatus(
        premium_requests=premium_requests,
        sample_duration_seconds=duration_seconds,
        premium_percent_left=premium_percent_left,
        premium_reset=premium_note,
        raw_text=clean,
    )
