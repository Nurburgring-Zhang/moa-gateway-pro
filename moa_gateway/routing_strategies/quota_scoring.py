"""Reset-aware / reset-window quota scoring — pure window math.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
``open-sse/services/combo/quotaScoring.ts`` — the pure scoring + window-math
half of the reset-aware quota block (``scoreResetAwareQuota``,
``getResetWindowRemainingMs``, ``resolveQuotaWindowByName``, the RESET_AWARE
defaults, tie-band rotation support). The stateful fetch/cache half of the
upstream module is replaced by the gateway's candidate model: quota snapshots
arrive already attached to ``EndpointCandidate.quota``.

No module state, no I/O — fully deterministic given (candidate, now_ms).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import QuotaSnapshot, QuotaWindow

# --- OmniRoute constants (quotaScoring.ts) ---------------------------------
RESET_AWARE_SESSION_WINDOW_MS = 5 * 60 * 60 * 1000
RESET_AWARE_WEEKLY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
RESET_AWARE_SESSION_REMAINING_WEIGHT = 0.45
RESET_AWARE_SESSION_RESET_PRESSURE_WEIGHT = 0.55
RESET_AWARE_WEEKLY_REMAINING_WEIGHT = 0.25
RESET_AWARE_WEEKLY_RESET_PRESSURE_WEIGHT = 0.75

RESET_AWARE_DEFAULTS = {
    "session_weight": 0.35,
    "weekly_weight": 0.65,
    "tie_band_percent": 5.0,
    "exhaustion_guard_percent": 10.0,
}
RESET_WINDOW_DEFAULT_TIE_BAND_MS = 60_000

RESET_WINDOW_NAMES = ("session", "weekly", "monthly")


def clamp01(value: float) -> float:
    """OmniRoute ``clamp01``: bound to [0,1]; non-finite -> 0."""
    if not math.isfinite(value):
        return 0.0
    if value <= 0:
        return 0.0
    if value >= 1:
        return 1.0
    return value


@dataclass(frozen=True)
class ResetAwareConfig:
    session_weight: float
    weekly_weight: float
    tie_band: float  # fraction, e.g. 0.05
    exhaustion_guard: float  # fraction, e.g. 0.10


@dataclass(frozen=True)
class ResetWindowConfig:
    windows: tuple[str, ...]
    tie_band_ms: float


def resolve_reset_aware_config(
    session_weight: float | None = None,
    weekly_weight: float | None = None,
    tie_band_percent: float | None = None,
    exhaustion_guard_percent: float | None = None,
) -> ResetAwareConfig:
    """Port of ``resolveResetAwareConfig`` (weights renormalised to sum 1)."""
    sw = session_weight if session_weight is not None and session_weight >= 0 else None
    ww = weekly_weight if weekly_weight is not None and weekly_weight >= 0 else None
    sw = RESET_AWARE_DEFAULTS["session_weight"] if sw is None else sw
    ww = RESET_AWARE_DEFAULTS["weekly_weight"] if ww is None else ww
    total = sw + ww
    if total > 0:
        sw_norm = sw / total
    else:
        sw_norm = RESET_AWARE_DEFAULTS["session_weight"]

    def _pct(value: float | None, fallback: float) -> float:
        if value is None or not math.isfinite(value):
            return fallback
        return max(0.0, min(100.0, value))

    return ResetAwareConfig(
        session_weight=sw_norm,
        weekly_weight=1 - sw_norm,
        tie_band=_pct(tie_band_percent, RESET_AWARE_DEFAULTS["tie_band_percent"]) / 100.0,
        exhaustion_guard=_pct(
            exhaustion_guard_percent, RESET_AWARE_DEFAULTS["exhaustion_guard_percent"]
        ) / 100.0,
    )


def resolve_reset_window_config(
    windows: list[str] | None = None,
    include_session: bool = False,
    tie_band_ms: float | None = None,
) -> ResetWindowConfig:
    """Port of ``resolveResetWindowConfig`` (default: weekly only)."""
    effective: list[str] = []
    if windows:
        for name in windows:
            if name in RESET_WINDOW_NAMES and name not in effective:
                effective.append(name)
    if not effective:
        effective = ["weekly", "session"] if include_session else ["weekly"]
    band = tie_band_ms if tie_band_ms is not None and math.isfinite(tie_band_ms) else None
    return ResetWindowConfig(
        windows=tuple(effective),
        tie_band_ms=max(0.0, RESET_WINDOW_DEFAULT_TIE_BAND_MS if band is None else band),
    )


# --- reset-instant parsing ---------------------------------------------------

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_NUMERIC_RE = re.compile(r"^\d+(?:\.\d+)?$")


def parse_reset_time_ms(reset_at: str | float | None, now_ms: float) -> float:
    """Port of ``parseResetTimeMs`` -> absolute epoch ms or NaN.

    Accepts ISO dates, numeric strings < 10_000_000_000 (epoch seconds,
    scaled ×1000) and >= threshold (epoch ms), and bare numbers.
    ``now_ms`` is accepted for API symmetry with the ported strategies that
    compute relative times; OmniRoute's absolute parse is clock-independent.
    """
    del now_ms  # absolute parse; relative consumers subtract their own clock.
    if reset_at is None:
        return math.nan
    if isinstance(reset_at, (int, float)):
        value = float(reset_at)
        if not math.isfinite(value):
            return math.nan
        return value * 1000 if value < 10_000_000_000 else value

    text = str(reset_at).strip()
    if not text:
        return math.nan
    # ISO / date-like strings first (Python handles 'Z' via fromisoformat 3.11+;
    # fall back to a tolerant parser below).
    parsed_iso = _parse_iso_ms(text)
    if parsed_iso is not None:
        return parsed_iso
    if not _NUMERIC_RE.match(text):
        return math.nan
    numeric = float(text)
    if not math.isfinite(numeric):
        return math.nan
    return numeric * 1000 if numeric < 10_000_000_000 else numeric


def _parse_iso_ms(text: str) -> float | None:
    if not _ISO_DATE_RE.match(text):
        return None
    from datetime import datetime, timezone

    candidate = text
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000


# --- window resolution (OmniRoute resolveQuotaWindowByName) ------------------


@dataclass(frozen=True)
class WindowSnapshot:
    percent_used: float | None
    reset_at: str | None


def _normalize_window_percent_used(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if value > 1:
        return clamp01(value / 100.0)
    return clamp01(value)


def _to_window_snapshot(window: QuotaWindow | None) -> WindowSnapshot | None:
    if window is None:
        return None
    return WindowSnapshot(
        percent_used=_normalize_window_percent_used(window.percent_used),
        reset_at=window.reset_at.strip() if isinstance(window.reset_at, str) and window.reset_at.strip() else None,
    )


def _pick_window_with_reset_at(
    *candidates: WindowSnapshot | None,
) -> WindowSnapshot | None:
    """OmniRoute ``pickWindowWithResetAt`` (#9330)."""
    for candidate in candidates:
        if candidate is not None and candidate.reset_at:
            return candidate
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def resolve_quota_window_by_name(
    quota: QuotaSnapshot | None, window_name: str
) -> WindowSnapshot | None:
    """Port of ``resolveQuotaWindowByName`` (structural fields + windows map)."""
    if quota is None:
        return None

    named: WindowSnapshot | None = None
    if window_name == "session":
        named = _to_window_snapshot(quota.window_5h)
    elif window_name == "weekly":
        named = _pick_window_with_reset_at(
            _to_window_snapshot(quota.window_7d),
            _to_window_snapshot(quota.window_weekly),
        )
    elif window_name == "monthly":
        named = _to_window_snapshot(quota.window_monthly)

    # windows-map lookup: exact key or "<name> <suffix>", sorted, preferring a
    # candidate that knows when it resets (OmniRoute getWindowsMapQuotaWindow).
    map_candidates: list[tuple[str, WindowSnapshot]] = []
    if quota.windows:
        for key, value in quota.windows.items():
            lowered = key.lower()
            if lowered == window_name or lowered.startswith(f"{window_name} "):
                snap = _to_window_snapshot(value)
                if snap is not None:
                    map_candidates.append((lowered, snap))
    map_pick: WindowSnapshot | None = None
    if map_candidates:
        map_candidates.sort(key=lambda entry: entry[0])
        with_reset = [snap for _, snap in map_candidates if snap.reset_at]
        map_pick = _pick_window_with_reset_at(*with_reset, map_candidates[0][1])

    return _pick_window_with_reset_at(named, map_pick)


def _earliest_window_reset_ms(quota: QuotaSnapshot, now_ms: float) -> float:
    """OmniRoute ``getEarliestWindowResetMs`` (any window name — #9330)."""
    earliest = math.inf
    seen: list[QuotaWindow | None] = []
    if quota.windows:
        seen.extend(quota.windows.values())
    for window in seen:
        snap = _to_window_snapshot(window)
        if snap is None:
            continue
        reset_ms = parse_reset_time_ms(snap.reset_at, now_ms)
        if math.isfinite(reset_ms):
            earliest = min(earliest, reset_ms)
    return earliest


# --- reset-aware scoring -------------------------------------------------------


def _get_reset_urgency(reset_at: str | None, window_ms: float, now_ms: float) -> float:
    if not reset_at:
        return 0.5
    reset_time = parse_reset_time_ms(reset_at, now_ms)
    if not math.isfinite(reset_time):
        return 0.5
    ms_until_reset = reset_time - now_ms
    if ms_until_reset <= 0:
        return 1.0
    return clamp01(1 - ms_until_reset / window_ms)


def _score_quota_window(
    remaining: float,
    reset_at: str | None,
    window_ms: float,
    remaining_weight: float,
    reset_pressure_weight: float,
    now_ms: float,
) -> float:
    normalized_remaining = clamp01(remaining)
    reset_urgency = _get_reset_urgency(reset_at, window_ms, now_ms)
    reset_pressure = reset_urgency * (1 - normalized_remaining)
    return remaining_weight * normalized_remaining + reset_pressure_weight * reset_pressure


def score_reset_aware_quota(
    quota: QuotaSnapshot | None, config: ResetAwareConfig, now_ms: float
) -> float:
    """Port of ``scoreResetAwareQuota`` — returns the [0,1] score (−inf when
    the provider reported ``limitReached``; 0.5 neutral when no quota data)."""
    if quota is None:
        return 0.5
    if quota.limit_reached:
        return -math.inf

    overall = clamp01(quota.percent_used if quota.percent_used is not None and math.isfinite(quota.percent_used) else 0.5)
    session_window = resolve_quota_window_by_name(quota, "session")
    weekly_window = resolve_quota_window_by_name(quota, "weekly")
    session_used = session_window.percent_used if session_window and session_window.percent_used is not None else overall
    weekly_used = weekly_window.percent_used if weekly_window and weekly_window.percent_used is not None else overall
    session_remaining = clamp01(1 - session_used)
    weekly_remaining = clamp01(1 - weekly_used)

    session_score = _score_quota_window(
        session_remaining,
        session_window.reset_at if session_window else None,
        RESET_AWARE_SESSION_WINDOW_MS,
        RESET_AWARE_SESSION_REMAINING_WEIGHT,
        RESET_AWARE_SESSION_RESET_PRESSURE_WEIGHT,
        now_ms,
    )
    weekly_reset_at = None
    if weekly_window and weekly_window.reset_at:
        weekly_reset_at = weekly_window.reset_at
    elif quota.reset_at and str(quota.reset_at).strip():
        weekly_reset_at = str(quota.reset_at).strip()
    weekly_score = _score_quota_window(
        weekly_remaining,
        weekly_reset_at,
        RESET_AWARE_WEEKLY_WINDOW_MS,
        RESET_AWARE_WEEKLY_REMAINING_WEIGHT,
        RESET_AWARE_WEEKLY_RESET_PRESSURE_WEIGHT,
        now_ms,
    )
    score = config.session_weight * session_score + config.weekly_weight * weekly_score

    # Exhaustion guard: near-empty session windows collapse the score.
    if config.exhaustion_guard > 0 and session_remaining < config.exhaustion_guard:
        score *= max(0.05, session_remaining / config.exhaustion_guard)
    return score


# --- reset-window remaining ----------------------------------------------------


def get_reset_window_timestamp_ms(
    quota: QuotaSnapshot | None, windows: tuple[str, ...], now_ms: float
) -> float:
    """Port of ``getResetWindowTimestampMs`` — earliest reset across the
    configured windows, then any named window, then top-level ``resetAt``."""
    if quota is None or quota.limit_reached:
        return math.inf

    selected = math.inf
    for window_name in windows:
        window = resolve_quota_window_by_name(quota, window_name)
        reset_ms = parse_reset_time_ms(window.reset_at if window else None, now_ms)
        if math.isfinite(reset_ms):
            selected = min(selected, reset_ms)

    if not math.isfinite(selected):
        selected = _earliest_window_reset_ms(quota, now_ms)

    if not math.isfinite(selected):
        selected = parse_reset_time_ms(quota.reset_at, now_ms)

    return selected if math.isfinite(selected) else math.inf


def get_reset_window_remaining_ms(
    quota: QuotaSnapshot | None, windows: tuple[str, ...], now_ms: float
) -> float:
    """Port of ``getResetWindowRemainingMs`` (Infinity = unknown → sorts last)."""
    reset_ms = get_reset_window_timestamp_ms(quota, windows, now_ms)
    if not math.isfinite(reset_ms):
        return math.inf
    return max(0.0, reset_ms - now_ms)


# --- headroom -------------------------------------------------------------------


def compute_headroom(util_5h: float | None, util_7d: float | None) -> float:
    """Port of ``computeHeadroom`` = 1 − max(util_5h, util_7d), fail-open."""

    def clamp_util(value: float | None) -> float:
        if value is None or not math.isfinite(value):
            return 0.0
        if value <= 0:
            return 0.0
        if value >= 1:
            return 1.0
        return value

    return 1 - max(clamp_util(util_5h), clamp_util(util_7d))


def candidate_window_utilization(
    quota: QuotaSnapshot | None, window_name: str
) -> float | None:
    """Utilization (0..1) of a named window, if the snapshot carries it."""
    if quota is None:
        return None
    window = resolve_quota_window_by_name(quota, window_name)
    if window is None or window.percent_used is None:
        return None
    return window.percent_used
