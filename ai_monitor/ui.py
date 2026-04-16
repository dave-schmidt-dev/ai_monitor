"""Terminal rendering for the usage dashboard."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .providers import ProviderSnapshot

THEME = Theme(
    {
        "bg": "on color(17)",
        "panel": "on color(236)",
        "panel_alt": "on color(234)",
        "text.ink": "color(254)",
        "text.muted": "color(250)",
        "text.blue": "color(111)",
        "text.cyan": "color(117)",
        "text.teal": "color(80)",
        "text.green": "color(114)",
        "text.yellow": "color(221)",
        "text.orange": "color(215)",
        "text.red": "color(203)",
        "text.pink": "color(219)",
        "border": "color(67)",
        "shadow": "color(239)",
        "bar.empty": "color(244)",
        "bar.green": "color(114)",
        "bar.yellow": "color(221)",
        "bar.orange": "color(215)",
        "bar.red": "color(203)",
        "accent.codex": "color(111)",
        "accent.claude": "color(219)",
        "accent.gemini": "color(80)",
        "accent.copilot": "color(117)",
        "accent.cursor": "color(214)",
        "accent.vibe": "color(208)",
    }
)


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
    windows: tuple[WindowRenderSpec, ...]


PROVIDER_RENDER_SPECS = {
    "Codex": ProviderRenderSpec(
        title="Codex",
        subtitle="Codex usage",
        windows=(
            WindowRenderSpec(
                "five_hour",
                "5h",
                "5h ↻",
                None,
                "five_hour_percent_left",
                "five_hour_reset",
                5.0,
            ),
            WindowRenderSpec(
                "weekly",
                "1w",
                "1w ↻",
                None,
                "weekly_percent_left",
                "weekly_reset",
                24.0 * 7.0,
            ),
        ),
    ),
    "Claude": ProviderRenderSpec(
        title="Claude",
        subtitle="Claude usage",
        windows=(
            WindowRenderSpec(
                "five_hour",
                "5h",
                "5h ↻",
                None,
                "session_percent_left",
                "primary_reset",
                5.0,
            ),
            WindowRenderSpec(
                "weekly",
                "1w",
                "1w ↻",
                None,
                "weekly_percent_left",
                "secondary_reset",
                24.0 * 7.0,
            ),
        ),
    ),
    "Gemini": ProviderRenderSpec(
        title="Gemini",
        subtitle="Gemini usage",
        windows=(
            WindowRenderSpec(
                "flash",
                "fl",
                "fl ↻",
                None,
                "flash_percent_left",
                "flash_reset",
                24.0,
            ),
            WindowRenderSpec(
                "pro",
                "pr",
                "pr ↻",
                None,
                "pro_percent_left",
                "pro_reset",
                24.0 * 30,
            ),
        ),
    ),
    "Copilot": ProviderRenderSpec(
        title="Copilot",
        subtitle="Copilot usage",
        windows=(
            WindowRenderSpec(
                "premium",
                "rem",
                "mo ↻",
                None,
                "premium_percent_left",
                "premium_reset",
                None,
            ),
        ),
    ),
}


# ---------------------------------------------------------------------------
# Shared helpers (business logic, kept from original)
# ---------------------------------------------------------------------------


def _plain(value: object | None) -> str:
    if value is None or value == "":
        return "n/a"
    return str(value)


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


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

    relative = re.search(
        r"(?i)\bin\s+(?:(?P<days>\d+)d\s*)?(?:(?P<hours>\d+)h\s*)?(?:(?P<minutes>\d+)m)?",
        fragments,
    )
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
    return f"{hour:02d}:{minute:02d}"


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


def _pace_label(
    percent_left: float | None,
    reset_text: str | None,
    now: datetime,
    window_hours: float | None,
) -> str:
    if percent_left is None or window_hours is None:
        return "n/a"
    target = _parse_reset_target(reset_text, now)
    if target is None:
        return "n/a"
    remaining_seconds = max(0.0, (target - now).total_seconds())
    total_seconds = window_hours * 3600.0
    if total_seconds <= 0:
        return "n/a"
    remaining_fraction = remaining_seconds / total_seconds
    percent_fraction = percent_left / 100.0
    delta = percent_fraction - remaining_fraction
    diff_points = round(abs(delta) * 100)
    if abs(delta) <= 0.05:
        return "on pace"
    if delta > 0:
        return f"under +{diff_points}pt"
    return f"over -{diff_points}pt"


def _format_percent_value(percent: float | None) -> str:
    if percent is None:
        return "n/a"
    return f"{round(percent)}%"


def _is_empty_window(percent: float | None) -> bool:
    """Return True if a usage window rounds to 0% remaining."""
    return percent is not None and round(percent) <= 0


def _provider_is_empty(snapshot: ProviderSnapshot) -> bool:
    """Return True when the provider should switch to the depleted/empty view."""
    if not snapshot.ok or not snapshot.data:
        return False
    data = snapshot.data
    name = snapshot.name.removesuffix(" [HTTP]")
    if name == "Codex":
        return _is_empty_window(data.get("five_hour_percent_left")) or _is_empty_window(
            data.get("weekly_percent_left")
        )
    if name == "Claude":
        return _is_empty_window(data.get("session_percent_left")) or _is_empty_window(
            data.get("weekly_percent_left")
        )
    if name == "Copilot":
        return _is_empty_window(data.get("premium_percent_left"))
    if name == "Cursor":
        return _is_empty_window(data.get("credit_percent_left"))
    if name == "Vibe":
        usage = data.get("usage_percent")
        pct_left = max(0.0, 100.0 - float(usage)) if isinstance(usage, (int, float)) else None
        return _is_empty_window(pct_left)
    if name == "Gemini":
        return _is_empty_window(data.get("flash_percent_left")) and _is_empty_window(
            data.get("pro_percent_left")
        )
    return False


def _copilot_monthly_reset_target(now: datetime) -> datetime:
    utc_now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    year = utc_now.year + (1 if utc_now.month == 12 else 0)
    month = 1 if utc_now.month == 12 else utc_now.month + 1
    return datetime(year, month, 1, 0, 0, tzinfo=timezone.utc)


def _billing_cycle_pace_label(
    percent_left: float | None,
    start_iso: str | None,
    end_iso: str | None,
    now: datetime,
) -> str:
    """Compute pace label for any billing cycle with known start and end dates."""
    if percent_left is None or not start_iso or not end_iso:
        return "n/a"
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except ValueError:
        return "n/a"
    total_seconds = max(1.0, (end - start).total_seconds())
    if start.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    elif start.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=start.tzinfo)
    remaining_seconds = max(0.0, (end - now).total_seconds())
    expected_remaining = (remaining_seconds / total_seconds) * 100.0
    delta = percent_left - expected_remaining
    diff_points = round(abs(delta))
    if abs(delta) <= 5.0:
        return "on pace"
    if delta > 0:
        return f"under +{diff_points}pt"
    return f"over -{diff_points}pt"


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


# ---------------------------------------------------------------------------
# Rich style helpers
# ---------------------------------------------------------------------------


def _style_for_percent(percent: float | None) -> str:
    """Return a Rich theme style name for a usage percentage."""
    if percent is None:
        return "text.muted"
    if percent >= 70:
        return "bar.green"
    if percent >= 40:
        return "bar.yellow"
    if percent >= 20:
        return "bar.orange"
    return "bar.red"


ACCENT_STYLES: dict[str, str] = {
    "Codex": "accent.codex",
    "Claude": "accent.claude",
    "Gemini": "accent.gemini",
    "Copilot": "accent.copilot",
    "Cursor": "accent.cursor",
    "Vibe": "accent.vibe",
}


# ---------------------------------------------------------------------------
# Rich renderables
# ---------------------------------------------------------------------------


class PercentageBar:
    """Custom Rich renderable: a static percentage bar using block characters."""

    def __init__(self, percent: float | None, style: str) -> None:
        self.percent = percent
        self.style = style

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        if self.percent is None:
            yield Text("·" * width, style="shadow")
            return
        filled = max(0, min(width, round(width * self.percent / 100)))
        empty = max(0, width - filled)
        bar = Text()
        if filled > 1:
            bar.append("▓" * (filled - 1), style=self.style)
        if filled > 0:
            bar.append("█", style=self.style)
        if empty > 0:
            bar.append("░" * empty, style="bar.empty")
        yield bar


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------


def build_provider_panel(
    snapshot: ProviderSnapshot,
    now: datetime,
    *,
    threshold: float = 20.0,
    auth_fix_key: str | None = None,
) -> Panel:
    """Build a Rich Panel for a single provider snapshot."""
    base_name = snapshot.name.removesuffix(" [HTTP]")
    accent = ACCENT_STYLES.get(base_name, "text.cyan")
    below_threshold = False
    if snapshot.ok and snapshot.data:
        for key in (
            "credit_percent_left",
            "premium_percent_left",
            "session_percent_left",
            "five_hour_percent_left",
            "flash_percent_left",
        ):
            value = snapshot.data.get(key)
            if isinstance(value, (int, float)) and float(value) < threshold:
                below_threshold = True
                break
        usage = snapshot.data.get("usage_percent")
        if isinstance(usage, (int, float)) and (100.0 - float(usage)) < threshold:
            below_threshold = True
    title_text = f"[bold {accent}]{snapshot.name}[/]"
    if below_threshold:
        title_text += " [bold text.red][!][/]"

    if not snapshot.ok:
        if auth_fix_key is not None:
            body = Text.from_markup(
                f"[text.red]auth error[/] [text.muted]— press [/]"
                f"[text.cyan]\\[{auth_fix_key}][/]"
                f"[text.muted] to fix[/]"
            )
        else:
            error_msg = _truncate(snapshot.error or "unknown error", 60)
            body = Text.from_markup(f"[text.red]error:[/] [text.muted]{error_msg}[/]")
        return Panel(
            body,
            title=title_text,
            border_style="text.red",
            padding=(0, 1),
        )

    assert snapshot.data is not None
    cached_badge = (
        _cached_badge_text(snapshot, now) if "cached" in snapshot.source.lower() else None
    )

    spec = PROVIDER_RENDER_SPECS.get(base_name)

    # All panels use the same 5-column layout so bars align across the grid:
    # label | % | bar | reset | pace
    body = Table.grid(padding=(0, 1))
    body.add_column(min_width=2, max_width=2)
    body.add_column(min_width=4, max_width=4)
    body.add_column(min_width=4, ratio=1)
    body.add_column(min_width=12, max_width=12)
    body.add_column(min_width=12, max_width=12)

    if _provider_is_empty(snapshot):
        _add_empty_view(body, snapshot, now)
    elif base_name == "Copilot":
        _add_copilot_rows(body, snapshot.data, now)
    elif base_name == "Cursor":
        _add_cursor_rows(body, snapshot.data, now)
    elif base_name == "Vibe":
        _add_vibe_rows(body, snapshot.data, now)
    elif spec:
        _add_usage_rows(body, snapshot.data, now, spec.windows)
    else:
        _add_generic_rows(body, snapshot.data, snapshot.source)

    panel_kwargs: dict[str, object] = {
        "title": title_text,
        "border_style": accent,
        "subtitle_align": "left",
        "padding": (0, 1),
    }
    if cached_badge:
        panel_kwargs["subtitle"] = f"[{accent}]{cached_badge}[/]"

    return Panel(body, **panel_kwargs)


def _pace_style(pace: str) -> str:
    if "under" in pace:
        return "text.green"
    if "over" in pace:
        return "text.red"
    if "on pace" in pace:
        return "text.yellow"
    return "text.muted"


def _add_usage_rows(
    table: Table,
    data: dict[str, object],
    now: datetime,
    windows: tuple[WindowRenderSpec, ...],
) -> None:
    """Add one row per window: label | % | bar | reset | pace."""
    for window in windows:
        percent = data.get(window.percent_key)
        reset = data.get(window.reset_key)
        reset_str = None if reset is None else str(reset)
        style = _style_for_percent(percent)
        reset_display = _format_reset_display(reset_str, now)
        pace = _pace_label(percent, reset_str, now, window.window_hours)
        table.add_row(
            Text(window.session_label, style="text.muted"),
            Text(_format_percent_value(percent), style=style),
            PercentageBar(percent, style),
            Text(reset_display, style="text.cyan"),
            Text(pace, style=_pace_style(pace)),
        )


def _add_empty_view(table: Table, snapshot: ProviderSnapshot, now: datetime) -> None:
    """All rows show depleted format — provider has no usable capacity.

    Depleted windows show their own reset. Non-depleted windows (still blocked
    because another window is at 0%) show the blocking window's reset time.
    """
    data = snapshot.data
    assert data is not None
    name = snapshot.name.removesuffix(" [HTTP]")
    _e = Text("")

    def _row(label: str, reset_str: str | None) -> None:
        reset_display = _format_reset_display(reset_str, now)
        table.add_row(
            Text(label, style="text.muted"),
            Text("0%", style="bar.red"),
            Text(f"until {reset_display}", style="text.red"),
            _e,
            _e,
        )

    spec = PROVIDER_RENDER_SPECS.get(name)
    if spec:
        # Find the reset of the first depleted window — that's what blocks usage.
        blocking_reset: str | None = None
        for window in spec.windows:
            if _is_empty_window(data.get(window.percent_key)):
                raw = data.get(window.reset_key)
                blocking_reset = None if raw is None else str(raw)
                break

        for window in spec.windows:
            percent = data.get(window.percent_key)
            if _is_empty_window(percent):
                raw = data.get(window.reset_key)
                _row(window.session_label, None if raw is None else str(raw))
            else:
                # Window has remaining capacity but provider is blocked;
                # show the blocking reset so the user knows when they can work again.
                _row(window.session_label, blocking_reset)
    elif name == "Copilot":
        reset_value = data.get("premium_reset") or (
            f"Resets {_copilot_monthly_reset_target(now).astimezone().strftime('%b %d at %H:%M')}"
        )
        _row("mo", str(reset_value))
    elif name == "Cursor":
        reset_value = data.get("billing_cycle_end")
        _row("mo", str(reset_value) if isinstance(reset_value, str) else None)
        plan_name = data.get("plan_name")
        if plan_name:
            table.add_row(
                Text("pl", style="text.muted"),
                Text(str(plan_name), style="text.ink"),
                _e,
                _e,
                _e,
            )
    elif name == "Vibe":
        reset_value = data.get("reset_at")
        _row("mo", str(reset_value) if isinstance(reset_value, str) else None)


def _add_copilot_rows(table: Table, data: dict[str, object], now: datetime) -> None:
    """Add Copilot-specific monthly metric rows."""
    remaining = data.get("premium_percent_left")
    percent_left = float(remaining) if isinstance(remaining, (int, float)) else None
    reset_value = (
        data.get("premium_reset")
        or f"Resets {_copilot_monthly_reset_target(now).astimezone().strftime('%b %d at %H:%M')}"
    )
    utc_now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    start = utc_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = _copilot_monthly_reset_target(utc_now)
    pace_text = _billing_cycle_pace_label(percent_left, start.isoformat(), end.isoformat(), utc_now)

    style = _style_for_percent(percent_left)
    value_text = _format_percent_value(percent_left)
    reset_display = _format_reset_display(None if reset_value is None else str(reset_value), now)
    table.add_row(
        Text("mo", style="text.muted"),
        Text(value_text, style=style),
        PercentageBar(percent_left, style),
        Text(reset_display, style="text.cyan"),
        Text(pace_text, style=_pace_style(pace_text)),
    )


def _add_cursor_rows(table: Table, data: dict[str, object], now: datetime) -> None:
    """Add Cursor credit usage rows."""
    percent_left = data.get("credit_percent_left")
    if isinstance(percent_left, (int, float)):
        percent_left = float(percent_left)
    else:
        percent_left = None

    reset_value = data.get("billing_cycle_end")
    plan_name = data.get("plan_name")

    style = _style_for_percent(percent_left)
    value_text = _format_percent_value(percent_left)
    reset_display = _format_reset_display(None if reset_value is None else str(reset_value), now)
    start_iso = data.get("billing_cycle_start")
    end_iso = data.get("billing_cycle_end_iso")
    pace_text = _billing_cycle_pace_label(
        percent_left,
        str(start_iso) if isinstance(start_iso, str) else None,
        str(end_iso) if isinstance(end_iso, str) else None,
        now,
    )
    table.add_row(
        Text("mo", style="text.muted"),
        Text(value_text, style=style),
        PercentageBar(percent_left, style),
        Text(reset_display, style="text.cyan"),
        Text(pace_text, style=_pace_style(pace_text)),
    )
    if plan_name:
        table.add_row(
            Text("pl", style="text.muted"),
            Text(str(plan_name), style="text.ink"),
            Text(""),
            Text(""),
            Text(""),
        )


def _add_vibe_rows(table: Table, data: dict[str, object], now: datetime) -> None:
    """Add Mistral Vibe monthly usage rows."""
    usage_percent = data.get("usage_percent")
    if isinstance(usage_percent, (int, float)):
        percent_left = max(0.0, 100.0 - float(usage_percent))
    else:
        percent_left = None

    reset_value = data.get("reset_at")

    style = _style_for_percent(percent_left)
    value_text = _format_percent_value(percent_left)
    reset_display = _format_reset_display(None if reset_value is None else str(reset_value), now)
    start_iso = data.get("start_date")
    end_iso = data.get("end_date")
    pace_text = _billing_cycle_pace_label(
        percent_left,
        str(start_iso) if isinstance(start_iso, str) else None,
        str(end_iso) if isinstance(end_iso, str) else None,
        now,
    )
    table.add_row(
        Text("mo", style="text.muted"),
        Text(value_text, style=style),
        PercentageBar(percent_left, style),
        Text(reset_display, style="text.cyan"),
        Text(pace_text, style=_pace_style(pace_text)),
    )


def _add_generic_rows(table: Table, data: dict[str, object], source: str) -> None:
    """Add generic key-value rows for unknown providers."""
    _e = Text("")
    table.add_row(Text("status", style="text.muted"), Text("ok", style="text.ink"), _e, _e, _e)
    table.add_row(Text("source", style="text.muted"), Text(source, style="text.ink"), _e, _e, _e)
    count = 0
    for key, value in sorted(data.items()):
        if key == "raw_text":
            continue
        label = key.replace("_", " ")[:12]
        table.add_row(
            Text(label, style="text.muted"),
            Text(_plain(value), style="text.ink"),
            _e,
            _e,
            _e,
        )
        count += 1
        if count >= 4:
            break


# ---------------------------------------------------------------------------
# Dashboard composition
# ---------------------------------------------------------------------------


def build_dashboard(
    snapshots: list[ProviderSnapshot],
    updated_at: datetime,
    next_refresh_seconds: int,
    *,
    updating: bool = False,
    update_elapsed: float = 0.0,
    threshold: float = 20.0,
) -> Group:
    """Build the full dashboard as a Rich Group."""
    now = updated_at

    # Header
    refresh_value = f"{update_elapsed:0.1f}s" if updating else f"{next_refresh_seconds}s"
    header = Text.assemble(
        ("AI Usage Monitor", "bold text.cyan"),
        ("  |  ", "text.muted"),
        ("Last Updated: ", "text.muted"),
        (updated_at.strftime("%b %d %H:%M:%S"), "text.yellow"),
        ("  |  ", "text.muted"),
        ("↻ ", "text.muted"),
        (refresh_value, "text.cyan"),
    )

    # Build panels — Cursor and Vibe are compact (3 rows); sort them last so they
    # always share a row rather than being paired with a taller provider.
    _COMPACT = {"Cursor", "Vibe"}
    ordered = sorted(snapshots, key=lambda s: s.name in _COMPACT)
    panels = [build_provider_panel(snap, now, threshold=threshold) for snap in ordered]

    # Layout: 2-column grid if multiple panels
    if len(panels) > 1:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        for i in range(0, len(panels), 2):
            if i + 1 < len(panels):
                grid.add_row(panels[i], panels[i + 1])
            else:
                grid.add_row(panels[i], Text(""))
        body: RenderableType = grid
    elif panels:
        body = panels[0]
    else:
        body = Text("No providers configured.", style="text.muted")

    footer = Text.assemble(
        ("[q]", "cyan"),
        " quit  ",
        ("[r]", "cyan"),
        " refresh",
    )

    return Group(header, Text(""), body, Text(""), footer)


def build_loading_screen(message: str, updated_at: datetime, elapsed_seconds: float = 0.0) -> Group:
    """Build the loading/startup screen as a Rich Group."""
    header = Text.assemble(
        ("AI Usage Monitor", "bold text.cyan"),
        ("  |  ", "text.muted"),
        ("Last Updated: ", "text.muted"),
        (updated_at.strftime("%b %d %H:%M:%S"), "text.yellow"),
        ("  |  ", "text.muted"),
        ("Starting up ", "text.muted"),
        (f"{elapsed_seconds:0.1f}s", "text.cyan"),
    )

    body = Table.grid(padding=(0, 1))
    body.add_column()
    spinner = Spinner("dots", text=Text(message, style="text.ink"), style="text.cyan")
    body.add_row(spinner)
    body.add_row(Text("First refresh can take a few seconds.", style="text.muted"))
    body.add_row(Text("PTY sessions are reused after startup.", style="text.muted"))

    panel = Panel(
        body,
        title="[bold text.cyan]Warming Up[/]",
        subtitle="[text.muted]getting initial usage[/]",
        border_style="border",
        subtitle_align="left",
        padding=(0, 1),
    )

    footer = Text.assemble(("[q]", "cyan"), " quit  ", ("[r]", "cyan"), " refresh")
    return Group(header, Text(""), panel, Text(""), footer)


# ---------------------------------------------------------------------------
# JSON output (no Rich dependency — plain string)
# ---------------------------------------------------------------------------


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
