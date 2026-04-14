"""Terminal rendering for the usage dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import shutil
import sys
import time

from .providers import ProviderSnapshot


CLEAR = "\033[2J\033[H"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

PALETTE = {
    "bg": "\033[48;5;17m",
    "panel": "\033[48;5;236m",
    "panel_alt": "\033[48;5;234m",
    "ink": "\033[38;5;254m",
    "muted": "\033[38;5;250m",
    "blue": "\033[38;5;111m",
    "cyan": "\033[38;5;117m",
    "teal": "\033[38;5;80m",
    "green": "\033[38;5;114m",
    "yellow": "\033[38;5;221m",
    "orange": "\033[38;5;215m",
    "red": "\033[38;5;203m",
    "pink": "\033[38;5;219m",
    "border": "\033[38;5;67m",
    "shadow": "\033[38;5;239m",
}


@dataclass(frozen=True, slots=True)
class WindowRenderSpec:
    window_id: str
    session_label: str
    reset_label: str
    pace_label: str | None
    percent_key: str
    reset_key: str
    window_hours: float | None


@dataclass(frozen=True, slots=True)
class ProviderRenderSpec:
    title: str
    subtitle: str
    accent: str
    windows: tuple[WindowRenderSpec, ...]


PROVIDER_RENDER_SPECS = {
    "Codex": ProviderRenderSpec(
        title="Codex",
        subtitle="OpenAI CLI quota view",
        accent=PALETTE["blue"],
        windows=(
            WindowRenderSpec("five_hour", "5h session", "5h resets", "5h pace", "five_hour_percent_left", "five_hour_reset", 5.0),
            WindowRenderSpec("weekly", "1w session", "1w resets", "1w pace", "weekly_percent_left", "weekly_reset", 24.0 * 7.0),
        ),
    ),
    "Claude": ProviderRenderSpec(
        title="Claude",
        subtitle="Anthropic CLI usage view",
        accent=PALETTE["pink"],
        windows=(
            WindowRenderSpec("five_hour", "5h session", "5h resets", "5h pace", "session_percent_left", "primary_reset", 5.0),
            WindowRenderSpec("weekly", "1w session", "1w resets", "1w pace", "weekly_percent_left", "secondary_reset", 24.0 * 7.0),
        ),
    ),
    "Gemini": ProviderRenderSpec(
        title="Gemini",
        subtitle="Google CLI usage view",
        accent=PALETTE["teal"],
        windows=(
            WindowRenderSpec("flash", "flash pool", "flash reset", None, "flash_percent_left", "flash_reset", None),
            WindowRenderSpec("pro", "pro pool", "pro reset", None, "pro_percent_left", "pro_reset", None),
        ),
    ),
    "Copilot": ProviderRenderSpec(
        title="Copilot",
        subtitle="GitHub Copilot CLI usage view",
        accent=PALETTE["cyan"],
        windows=(
            WindowRenderSpec("premium", "month rem", "month reset", "month pace", "premium_percent_left", "premium_reset", None),
        ),
    ),
}


def _terminal_width() -> int:
    cols = shutil.get_terminal_size(fallback=(96, 30)).columns
    safe_cols = max(56, cols - 4)
    return min(safe_cols, 152)


def _plain(value: object | None) -> str:
    if value is None or value == "":
        return "n/a"
    return str(value)


def _color_for_percent(percent: float | None) -> str:
    if percent is None:
        return PALETTE["muted"]
    if percent >= 70:
        return PALETTE["green"]
    if percent >= 40:
        return PALETTE["yellow"]
    if percent >= 20:
        return PALETTE["orange"]
    return PALETTE["red"]


def _badge(text: str, fg: str, bg: str = PALETTE["panel_alt"]) -> str:
    return f"{bg}{fg} {text} {RESET}"


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _visible_length(text: str) -> int:
    length = 0
    in_escape = False
    for char in text:
        if char == "\033":
            in_escape = True
        elif in_escape and char == "m":
            in_escape = False
        elif not in_escape:
            length += 1
    return length


def _pad_colored(text: str, width: int) -> str:
    padding = max(0, width - _visible_length(text))
    return f"{text}{' ' * padding}"


def _truncate_colored(text: str, width: int) -> str:
    if _visible_length(text) <= width:
        return text
    if width <= 1:
        return "…"

    result: list[str] = []
    visible = 0
    idx = 0
    while idx < len(text):
        char = text[idx]
        if char == "\033":
            end = idx + 1
            while end < len(text) and text[end] != "m":
                end += 1
            end = min(end + 1, len(text))
            result.append(text[idx:end])
            idx = end
            continue
        if visible >= width - 1:
            break
        result.append(char)
        visible += 1
        idx += 1
    result.append("…")
    result.append(RESET)
    return "".join(result)


def _apply_right_gutter(lines: list[str], width: int, gutter: int = 4) -> list[str]:
    limit = max(20, width - gutter)
    return [_truncate_colored(line, limit) for line in lines]


def _progress_bar(percent: float | None, width: int, accent: str) -> str:
    if percent is None:
        empty = "·" * width
        return f"{PALETTE['shadow']}{empty}{RESET}"

    filled = max(0, min(width, round(width * percent / 100)))
    empty = max(0, width - filled)
    head = "▓" * max(0, filled - 1)
    cap = "█" if filled else ""
    tail = "░" * empty
    bar = f"{accent}{head}{cap}{RESET}{PALETTE['shadow']}{tail}{RESET}"
    return bar


def _parse_reset_target(reset_text: str | None, now: datetime) -> datetime | None:
    if not reset_text or reset_text == "n/a":
        return None
    normalized = re.sub(r"\s+", " ", reset_text).strip()
    lower = normalized.lower()
    target: datetime | None = None
    target_year = now.year
    fragments = normalized.split("(", 1)[0].replace("resets", "").replace("Resets", "").strip()
    fragments = fragments.replace(",", "")
    fragments = re.sub(r"(?i)(\d)(am|pm)\b", r"\1 \2", fragments)
    fragments = re.sub(r"\s+", " ", fragments).strip()

    relative = re.search(r"(?i)\bin\s+(?:(?P<days>\d+)d\s*)?(?:(?P<hours>\d+)h\s*)?(?:(?P<minutes>\d+)m)?", fragments)
    if relative and any(relative.group(name) for name in ("days", "hours", "minutes")):
        return now + timedelta(
            days=int(relative.group("days") or 0),
            hours=int(relative.group("hours") or 0),
            minutes=int(relative.group("minutes") or 0),
        )

    if " on " in lower:
        candidates = [fragments]
        if fragments.lower().startswith("on "):
            candidates.insert(0, fragments[3:].strip())
        for candidate in candidates:
            stamped = f"{candidate} {target_year}"
            for fmt in (
                "%H:%M on %d %b %Y",
                "%I %p on %d %b %Y",
                "%I:%M %p on %d %b %Y",
                "%b %d %H:%M %Y",
                "%b %d %I %p %Y",
                "%b %d %I:%M %p %Y",
            ):
                try:
                    parsed = datetime.strptime(stamped, fmt)
                except ValueError:
                    continue
                target = parsed
                if target < now:
                    target = target.replace(year=target_year + 1)
                break
            if target is not None:
                break
    elif " at " in lower:
        stamped = f"{fragments} {target_year}"
        for fmt in (
            "%b %d at %H:%M %Y",
            "%b %d at %I %p %Y",
            "%b %d at %I:%M %p %Y",
            "%d %b at %H:%M %Y",
            "%d %b at %I %p %Y",
            "%d %b at %I:%M %p %Y",
        ):
            try:
                parsed = datetime.strptime(stamped, fmt)
            except ValueError:
                continue
            target = parsed
            if target < now:
                target = target.replace(year=target_year + 1)
            break
    elif lower.startswith("resets "):
        for fmt in ("%H:%M", "%I %p", "%I:%M %p"):
            try:
                parsed = datetime.strptime(fragments, fmt)
            except ValueError:
                continue
            target = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            if target < now:
                target = target + timedelta(days=1)
            break

    return target


def _countdown_label(reset_text: str | None, now: datetime) -> str | None:
    target = _parse_reset_target(reset_text, now)
    if target is None:
        return None
    delta = target - now
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 0:
        return None
    hours, minutes = divmod(total_minutes, 60)
    days, hours = divmod(hours, 24)
    if days > 0:
        return f"in {days}d {hours}h"
    if hours > 0:
        return f"in {hours}h {minutes}m"
    return f"in {minutes}m"


def _format_clock(hour: int, minute: int) -> str:
    stamp = datetime(2000, 1, 1, hour, minute).strftime("%I:%M %p")
    return stamp.lstrip("0")


def _cached_badge_text(snapshot: ProviderSnapshot, now: datetime) -> str:
    if not snapshot.cached_since:
        return "live"
    age_seconds = max(0, int((now - snapshot.cached_since).total_seconds()))
    if age_seconds < 60:
        return "cached <1m"
    age_minutes = age_seconds // 60
    if age_minutes < 60:
        return f"cached {age_minutes}m"
    age_hours = age_minutes // 60
    return f"cached {age_hours}h"


def _format_reset_display(reset_text: str | None, now: datetime) -> str:
    if not reset_text or reset_text == "n/a":
        return "n/a"
    target = _parse_reset_target(reset_text, now)
    if target is None:
        value = reset_text.replace("Resets", "").replace("resets", "").strip()
        return re.sub(r"\s+", " ", value)

    if target.date() != now.date():
        return target.strftime("%b %d ") + _format_clock(target.hour, target.minute)

    return _format_clock(target.hour, target.minute)


def _pace_label(percent_left: float | None, reset_text: str | None, now: datetime, window_hours: float | None) -> str:
    if percent_left is None or window_hours is None:
        return "pace n/a"
    target = _parse_reset_target(reset_text, now)
    if target is None:
        return "pace n/a"
    remaining_seconds = max(0.0, (target - now).total_seconds())
    total_seconds = window_hours * 3600.0
    if total_seconds <= 0:
        return "pace n/a"
    remaining_fraction = remaining_seconds / total_seconds
    percent_fraction = percent_left / 100.0
    delta = percent_fraction - remaining_fraction
    diff_points = round(abs(delta) * 100)
    if abs(delta) <= 0.05:
        return "on pace"
    if delta > 0:
        return f"under pace +{diff_points}pt"
    return f"over pace -{diff_points}pt"


def _format_percent_value(percent: float | None) -> str:
    if percent is None:
        return "n/a"
    rounded = round(percent, 1)
    if rounded.is_integer():
        return f"{int(rounded)}%"
    return f"{rounded:.1f}%"


def _metric_row(label: str, percent: float | None, reset_text: str | None, width: int, now: datetime) -> str:
    label_width = 9
    accent = _color_for_percent(percent)
    value_text = _format_percent_value(percent)
    left = f"{PALETTE['muted']}{label:<{label_width}}{RESET} {accent}{value_text}{RESET}"
    left_width = label_width + 1 + len(value_text)
    bar_width = max(8, width - left_width - 2)
    bar = _progress_bar(percent, bar_width, accent)
    return f"{left}  {bar}"


def _copilot_metric_row(label: str, percent: float | None, width: int) -> str:
    label_width = 9
    accent = _color_for_percent(percent)
    value_text = _plain(None if percent is None else f"{percent:.1f}%")
    left = f"{PALETTE['muted']}{label:<{label_width}}{RESET} {accent}{value_text}{RESET}"
    left_width = label_width + 1 + len(value_text)
    bar_width = max(8, width - left_width - 2)
    bar = _progress_bar(percent, bar_width, accent)
    return f"{left}  {bar}"


def _pace_row(label: str, percent: float | None, reset_text: str | None, width: int, now: datetime, window_hours: float | None) -> str:
    label_width = 9
    pace = _pace_label(percent, reset_text, now, window_hours)
    if "under pace" in pace:
        color = PALETTE["green"]
    elif "over pace" in pace:
        color = PALETTE["red"]
    elif "on pace" in pace:
        color = PALETTE["yellow"]
    else:
        color = PALETTE["muted"]
    plain = _truncate(pace, max(8, width - (label_width + 3)))
    return f"{PALETTE['muted']}{label:<{label_width}}{RESET} {color}{plain}{RESET}"


def _info_row(label: str, value: object | None, width: int) -> str:
    label_width = 9
    plain = _truncate(_plain(value), max(8, width - (label_width + 3)))
    return f"{PALETTE['muted']}{label:<{label_width}}{RESET} {PALETTE['ink']}{plain}{RESET}"


def _reset_row(label: str, value: object | None, width: int, now: datetime) -> str:
    label_width = 9
    plain = _truncate(_format_reset_display(None if value is None else str(value), now), max(8, width - (label_width + 3)))
    return f"{PALETTE['muted']}{label:<{label_width}}{RESET} {PALETTE['cyan']}{plain}{RESET}"


def _build_usage_rows(
    data: dict[str, object],
    width: int,
    now: datetime,
    windows: tuple[WindowRenderSpec, ...],
) -> list[str]:
    rows: list[str] = []
    for window in windows:
        percent = data.get(window.percent_key)
        reset = data.get(window.reset_key)
        rows.extend(
            (
                _metric_row(window.session_label, percent, None if reset is None else str(reset), width, now),
                _reset_row(window.reset_label, reset, width, now),
            )
        )
        if window.pace_label:
            rows.append(_pace_row(window.pace_label, percent, None if reset is None else str(reset), width, now, window.window_hours))
    return rows


def _generic_snapshot_rows(data: dict[str, object], width: int, source: str) -> list[str]:
    rows = [_info_row("status", "ok", width), _info_row("source", source, width)]
    for key, value in sorted(data.items()):
        if key == "raw_text":
            continue
        label = key.replace("_", " ")
        rows.append(_info_row(label[:12], value, width))
    return rows[:6]


def _provider_card(snapshot: ProviderSnapshot, card_width: int, now: datetime) -> list[str]:
    assert snapshot.data is not None
    badge_text = _cached_badge_text(snapshot, now) if "cached" in snapshot.source.lower() else "live"
    if snapshot.name == "Copilot":
        rows = _copilot_rows(snapshot.data, card_width - 4, now)
        return _card("Copilot", "GitHub Copilot CLI usage view", rows, card_width, PALETTE["cyan"], True, badge_text)
    spec = PROVIDER_RENDER_SPECS.get(snapshot.name)
    if spec is None:
        rows = _generic_snapshot_rows(snapshot.data, card_width - 4, snapshot.source)
        return _card(snapshot.name, "provider usage view", rows, card_width, PALETTE["cyan"], True, badge_text)

    rows = _build_usage_rows(snapshot.data, card_width - 4, now, spec.windows)
    return _card(spec.title, spec.subtitle, rows, card_width, spec.accent, True, badge_text)


def _copilot_monthly_reset_target(now: datetime) -> datetime:
    utc_now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    year = utc_now.year + (1 if utc_now.month == 12 else 0)
    month = 1 if utc_now.month == 12 else utc_now.month + 1
    return datetime(year, month, 1, 0, 0, tzinfo=timezone.utc)


def _copilot_monthly_pace_label(percent_left: float | None, now: datetime) -> str:
    if percent_left is None:
        return "pace n/a"
    utc_now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    start = utc_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = _copilot_monthly_reset_target(utc_now)
    total_seconds = max(1.0, (end - start).total_seconds())
    remaining_seconds = max(0.0, (end - utc_now).total_seconds())
    expected_remaining = (remaining_seconds / total_seconds) * 100.0
    delta = percent_left - expected_remaining
    diff_points = round(abs(delta), 1)
    if abs(delta) <= 5.0:
        return "on pace"
    if delta > 0:
        return f"under pace +{diff_points:.1f}pt"
    return f"over pace -{diff_points:.1f}pt"


def _copilot_pace_row(label: str, pace_text: str, width: int) -> str:
    label_width = 10
    if "under pace" in pace_text:
        color = PALETTE["green"]
    elif "over pace" in pace_text:
        color = PALETTE["red"]
    elif "on pace" in pace_text:
        color = PALETTE["yellow"]
    else:
        color = PALETTE["muted"]
    plain = _truncate(pace_text, max(8, width - (label_width + 3)))
    return f"{PALETTE['muted']}{label:<{label_width}}{RESET} {color}{plain}{RESET}"


def _copilot_rows(data: dict[str, object], width: int, now: datetime) -> list[str]:
    remaining = data.get("premium_percent_left")
    percent_left = float(remaining) if isinstance(remaining, (int, float)) else None
    reset_value = data.get("premium_reset") or f"Resets {_copilot_monthly_reset_target(now).strftime('%b %d %I:%M %p UTC')}"
    pace_text = _copilot_monthly_pace_label(percent_left, now)
    return [
        _copilot_metric_row("month rem", percent_left, width),
        _reset_row("month reset", reset_value, width, now),
        _copilot_pace_row("month pace", pace_text, width),
    ]


def _provider_display_fields(snapshot: ProviderSnapshot, now: datetime) -> dict[str, str]:
    if not snapshot.data:
        return {}
    spec = PROVIDER_RENDER_SPECS.get(snapshot.name)
    if spec is None:
        return {}
    display: dict[str, str] = {}
    for window in spec.windows:
        raw_reset = snapshot.data.get(window.reset_key)
        display[f"{window.window_id}_reset_display"] = _format_reset_display(
            None if raw_reset is None else str(raw_reset),
            now,
        )
    return display


def _card(
    title: str,
    subtitle: str,
    rows: list[str],
    width: int,
    accent: str,
    ok: bool,
    badge_text: str | None = None,
) -> list[str]:
    inner = width - 4
    top = f"{PALETTE['border']}+{'-' * (width - 2)}+{RESET}"
    bottom = f"{PALETTE['border']}+{'-' * (width - 2)}+{RESET}"
    safe_title = _truncate(title, inner - 2)
    safe_subtitle = _truncate(subtitle, inner)
    colored_title = f"{BOLD}{accent}{safe_title}{RESET}"
    title_line = f"{PALETTE['border']}|{RESET} {_pad_colored(colored_title, inner)} {PALETTE['border']}|{RESET}"
    subtitle_badge = _badge(badge_text or ("live" if ok else "issue"), PALETTE["ink"], PALETTE["panel"])
    badge_line = f"{PALETTE['border']}|{RESET} {_pad_colored(subtitle_badge, inner)} {PALETTE['border']}|{RESET}"
    subtitle_line = f"{PALETTE['border']}|{RESET} {_pad_colored(safe_subtitle, inner)} {PALETTE['border']}|{RESET}"
    body = [
        f"{PALETTE['border']}|{RESET} {_pad_colored(_truncate_colored(row, inner), inner)} {PALETTE['border']}|{RESET}"
        for row in rows
    ]
    return [top, title_line, badge_line, subtitle_line, *body, bottom]


def _merge_columns(left: list[str], right: list[str], gap: int = 2) -> list[str]:
    left_width = max(_visible_length(line) for line in left)
    right_width = max(_visible_length(line) for line in right)
    height = max(len(left), len(right))
    rows: list[str] = []
    for idx in range(height):
        left_line = left[idx] if idx < len(left) else " " * left_width
        right_line = right[idx] if idx < len(right) else " " * right_width
        rows.append(f"{_pad_colored(left_line, left_width)}{' ' * gap}{right_line}")
    return rows


def _merge_card_grid(cards: list[list[str]], gap: int = 2, columns: int = 2) -> list[str]:
    rows: list[str] = []
    for start in range(0, len(cards), columns):
        row_cards = cards[start : start + columns]
        merged = row_cards[0]
        for card in row_cards[1:]:
            merged = _merge_columns(merged, card, gap=gap)
        if rows:
            rows.append("")
        rows.extend(merged)
    return rows


def render_json(snapshots: list[ProviderSnapshot], updated_at: datetime) -> str:
    payload = {
        "updated_at": updated_at.isoformat(),
        "providers": [
            {
                "name": snap.name,
                "ok": snap.ok,
                "source": snap.source,
                "data": snap.data,
                "display": _provider_display_fields(snap, updated_at),
                "error": snap.error,
            }
            for snap in snapshots
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_loading_screen(message: str, updated_at: datetime, frame: int = 0, elapsed_seconds: float = 0.0) -> str:
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[frame % 10]
    width = _terminal_width()
    card_width = min(68, max(60, width - 8))
    hero = [
        f"{BOLD}{PALETTE['cyan']}AI Usage Monitor{RESET}  {_badge(updated_at.strftime('%a %b %d %I:%M:%S %p'), PALETTE['ink'])} {_badge(f'startup {elapsed_seconds:0.1f}s', PALETTE['cyan'])}",
        f"{DIM}{PALETTE['muted']}PTY-driven live usage scrape for local Codex, Claude, Gemini, and Copilot sessions{RESET}",
        "",
    ]
    rows = [
        f"{PALETTE['cyan']}{spinner}{RESET} {PALETTE['ink']}{message}{RESET}",
        f"{PALETTE['muted']}First refresh can take a few seconds.{RESET}",
        f"{PALETTE['muted']}PTY sessions are reused after startup.{RESET}",
    ]
    card = _card("Warming Up", "getting initial usage", rows, card_width, PALETTE["cyan"], True)
    footer = [
        "",
        f"{DIM}{PALETTE['muted']}Ctrl-C to exit.{RESET}",
    ]
    lines = [*hero, *card, *footer]
    return "\n".join(_apply_right_gutter(lines, width))


def render_screen(
    snapshots: list[ProviderSnapshot],
    updated_at: datetime,
    next_refresh_seconds: int,
    *,
    updating: bool = False,
    update_elapsed: float = 0.0,
    update_frame: int = 0,
) -> str:
    width = _terminal_width()
    now = updated_at

    header_title = f"{BOLD}{PALETTE['cyan']}AI Usage Monitor{RESET}"
    if updating:
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[update_frame % 10]
        refresh_badge = _badge(f"updating {spinner} {update_elapsed:0.1f}s", PALETTE["cyan"])
    else:
        refresh_badge = _badge(f"refresh {next_refresh_seconds:02d}s", PALETTE["cyan"])
    header_meta = f"{_badge(updated_at.strftime('%a %b %d %I:%M:%S %p'), PALETTE['ink'])} {refresh_badge}"
    hero = [
        f"{header_title}  {header_meta}",
        f"{DIM}{PALETTE['muted']}PTY-driven live usage scrape for local Codex, Claude, Gemini, and Copilot sessions{RESET}",
        "",
    ]

    cards: list[list[str]] = []
    gap = 2
    compact_split_width = 40
    wide_split_width = 42
    split_threshold = (compact_split_width * 2) + gap + 2
    extra_split_threshold = (wide_split_width * 2) + gap + 2
    if width >= extra_split_threshold:
        card_width = wide_split_width
    elif width >= split_threshold:
        card_width = compact_split_width
    else:
        card_width = min(68, max(60, width - 8))

    for snapshot in snapshots:
        if not snapshot.ok:
            error_rows = [
                _info_row("status", "error", card_width - 4),
                _info_row("source", snapshot.source, card_width - 4),
                f"{PALETTE['red']}{_truncate(snapshot.error or 'unknown error', card_width - 16)}{RESET}",
            ]
            cards.append(_card(snapshot.name, "probe needs attention", error_rows, card_width, PALETTE["red"], False))
            continue

        cards.append(_provider_card(snapshot, card_width, now))

    if len(cards) > 1 and width >= split_threshold:
        body = _merge_card_grid(cards, gap=gap, columns=2)
    else:
        body = []
        for idx, card in enumerate(cards):
            if idx:
                body.append("")
            body.extend(card)

    footer = [
        "",
        f"{DIM}{PALETTE['muted']}Ctrl-C to exit. Use --json for machine-readable output and --debug for raw capture tails.{RESET}",
    ]
    lines = [*hero, *body, *footer]
    return "\n".join(_apply_right_gutter(lines, width))


def write_screen(text: str, *, repaint: bool = False) -> None:
    if repaint:
        sys.stdout.write(CLEAR)
    sys.stdout.write(text)
    sys.stdout.flush()


def countdown_sleep(seconds: int, render_frame: callable) -> None:
    """Sleep while updating the dashboard countdown once per second."""

    for remaining in range(seconds, 0, -1):
        render_frame(remaining)
        time.sleep(1)
