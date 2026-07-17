"""rate_quota — 多窗口 token 配额(5h / weekly / monthly)+ ETA 耗尽估算

来源: 01 gateswarm-router / 02 MoA-together-ai

真实实现:
- 3 个独立时间窗口,每个有独立 token 限额
  * 5h: 滑窗 5*3600s
  * weekly: 滑窗 7*86400s
  * monthly: 滑窗 30*86400s
- record_usage 推进 used_tokens + 写入 used_history(时间戳,token)
- 任何时刻 prune 窗口外的 history(只保留 [now-window, now] 内的)
- check_available: 至少一个窗口余量 >= requested → 通过
- eta_exhaustion: 给定 burn_rate(每小时 token 消耗),算多少小时后用尽
  * 公式: hours = remaining_tokens / burn_rate
  * None = 已耗尽(remaining<=0) 或 burn_rate<=0
- would_exceed_within: 预测 horizon 小时内是否会超限
  * 预测用量 = current_used + burn_rate * horizon
  * 超限 = 预测用量 > 限额
- rolling_remaining: 实时算 = limit - 窗口内 history 总和(若 history 为空则用 used_tokens)

非 mock,所有计算为确定性的数学公式。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ============ Constants ============

WINDOW_5H_SECONDS = 5 * 3600           # 5 hours
WINDOW_WEEKLY_SECONDS = 7 * 86400       # 7 days
WINDOW_MONTHLY_SECONDS = 30 * 86400     # 30 days

WINDOW_DURATIONS: dict[str, int] = {
    "5h": WINDOW_5H_SECONDS,
    "weekly": WINDOW_WEEKLY_SECONDS,
    "monthly": WINDOW_MONTHLY_SECONDS,
}

VALID_WINDOW_NAMES = tuple(WINDOW_DURATIONS.keys())


# ============ Dataclasses ============

@dataclass
class QuotaWindow:
    """单个配额窗口(5h / weekly / monthly 三选一)"""
    name: str
    limit_tokens: int
    used_tokens: int = 0
    # (timestamp, tokens) 历史记录,按时间戳升序追加,prune 旧记录
    used_history: list[tuple[float, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.name not in WINDOW_DURATIONS:
            raise ValueError(
                f"window name must be one of {VALID_WINDOW_NAMES}, got {self.name!r}"
            )
        if self.limit_tokens <= 0:
            raise ValueError(f"limit_tokens must be > 0, got {self.limit_tokens}")
        if self.used_tokens < 0:
            raise ValueError(f"used_tokens must be >= 0, got {self.used_tokens}")
        # used_history 内部元素验证
        for ts, tk in self.used_history:
            if ts < 0:
                raise ValueError(f"history timestamp must be >= 0, got {ts}")
            if tk < 0:
                raise ValueError(f"history tokens must be >= 0, got {tk}")
        # 防御性拷贝(避免外部 mutate 内部状态)
        self.used_history = list(self.used_history)

    @property
    def window_seconds(self) -> int:
        return WINDOW_DURATIONS[self.name]

    def remaining(self) -> int:
        """剩余配额 = limit - used(可负,表示已超)"""
        return self.limit_tokens - self.used_tokens


@dataclass
class QuotaState:
    """多窗口配额总状态"""
    windows: dict[str, QuotaWindow]
    last_updated: float = 0.0

    def __post_init__(self) -> None:
        # 至少要有合法窗口名
        for name in self.windows:
            if name not in WINDOW_DURATIONS:
                raise ValueError(
                    f"unknown window name {name!r}, expected one of {VALID_WINDOW_NAMES}"
                )
        if not self.windows:
            raise ValueError("windows must not be empty")
        if self.last_updated < 0:
            raise ValueError(f"last_updated must be >= 0, got {self.last_updated}")

    def get(self, name: str) -> QuotaWindow:
        if name not in self.windows:
            raise KeyError(f"window {name!r} not in state")
        return self.windows[name]


# ============ Helpers ============

def _prune_history(window: QuotaWindow, now: float) -> None:
    """就地 prune 窗口外历史记录(只保留 [now-window_seconds, now] 区间)"""
    cutoff = now - window.window_seconds
    # 假设 history 已按时间戳升序;从左侧 pop 掉 < cutoff 的
    # 若不保证有序,此处会全扫一遍(复杂度 O(n),但正确)
    new_hist: list[tuple[float, int]] = []
    for ts, tk in window.used_history:
        if ts >= cutoff:
            new_hist.append((ts, tk))
    window.used_history = new_hist


def _recompute_used_from_history(window: QuotaWindow, now: float) -> None:
    """根据窗口内 history 重算 used_tokens(滑窗语义)"""
    cutoff = now - window.window_seconds
    total = 0
    for ts, tk in window.used_history:
        if ts >= cutoff:
            total += tk
    window.used_tokens = total


# ============ Core API ============

def record_usage(
    state: QuotaState,
    tokens: int,
    at: float | None = None,
) -> None:
    """记录一次 token 使用。

    真实逻辑:
    - 给所有窗口的 used_tokens 加上 tokens
    - 给所有窗口的 used_history 追加 (at, tokens)
    - prune 每个窗口外的 history
    - 重新计算 used_tokens 为窗口内 history 之和(滑窗语义)
    - 更新 state.last_updated = at
    """
    if tokens < 0:
        raise ValueError(f"tokens must be >= 0, got {tokens}")
    if at is None:
        at = state.last_updated if state.last_updated > 0 else 0.0
    if at < 0:
        raise ValueError(f"at (timestamp) must be >= 0, got {at}")

    for w in state.windows.values():
        w.used_tokens += tokens
        w.used_history.append((float(at), int(tokens)))

    # prune + recompute(以最后一次 append 的 at 作为 now)
    for w in state.windows.values():
        _prune_history(w, at)
        _recompute_used_from_history(w, at)

    state.last_updated = float(at)


def check_available(state: QuotaState, requested: int) -> tuple[bool, str]:
    """检查是否有窗口能容纳 requested tokens。

    真实逻辑:遍历所有窗口,至少一个满足 limit - used >= requested
    - True  → ("ok", "all within limits" 或 "window X has N free")
    - False → ("insufficient", 描述哪个窗口是最小限制,以及差距)
    """
    if requested < 0:
        raise ValueError(f"requested must be >= 0, got {requested}")

    feasible: list[tuple[str, int]] = []  # (window_name, free)
    min_gap: tuple[str, int] | None = None  # 最受限窗口

    for name, w in state.windows.items():
        free = w.remaining()
        if free >= requested:
            feasible.append((name, free))
        # 记录最受限(负 free 越小越受限,正 free 越小越受限)
        gap = free - requested
        if min_gap is None or gap < min_gap[1]:
            min_gap = (name, gap)

    if feasible:
        # 选最紧的可行窗口(更精确)
        feasible.sort(key=lambda x: x[1])
        name, free = feasible[0]
        return (True, f"ok: window {name!r} has {free} tokens free >= {requested}")

    # 不可行
    if min_gap is None:
        return (False, "insufficient: no windows defined")
    name, gap = min_gap
    return (
        False,
        f"insufficient: window {name!r} most constrained, "
        f"short by {-gap} tokens (requested {requested})",
    )


def eta_exhaustion(
    state: QuotaState,
    burn_rate_per_hour: float,
    window_name: str,
) -> float | None:
    """计算指定窗口耗尽 ETA(小时)。

    真实逻辑:
    - remaining = rolling_remaining(state, window_name)
    - 若 remaining <= 0:返回 None(已耗尽)
    - 若 burn_rate_per_hour <= 0:返回 None(无法估算)
    - 否则: hours = remaining / burn_rate_per_hour
    """
    if window_name not in state.windows:
        raise KeyError(f"window {window_name!r} not in state")

    remaining = rolling_remaining(state, window_name)
    if remaining <= 0:
        return None
    if burn_rate_per_hour <= 0:
        return None
    return remaining / burn_rate_per_hour


def would_exceed_within(
    state: QuotaState,
    requested: int,
    horizon_hours: float,
    burn_rate: float,
) -> bool:
    """预测 horizon 小时内是否超限(任一窗口超限即返回 True)。

    真实逻辑:
    - 对每个窗口:current_used(rolling) + requested + burn_rate * horizon_hours
    - 若 > limit:该窗口超限
    - 任一窗口超限 → True;全部安全 → False
    """
    if requested < 0:
        raise ValueError(f"requested must be >= 0, got {requested}")
    if horizon_hours < 0:
        raise ValueError(f"horizon_hours must be >= 0, got {horizon_hours}")
    if burn_rate < 0:
        raise ValueError(f"burn_rate must be >= 0, got {burn_rate}")

    now = state.last_updated
    for _name, w in state.windows.items():
        cutoff = now - w.window_seconds
        current_used = sum(tk for ts, tk in w.used_history if ts >= cutoff)
        predicted = current_used + requested + burn_rate * horizon_hours
        if predicted > w.limit_tokens:
            return True
    return False


def rolling_remaining(state: QuotaState, window_name: str) -> int:
    """实时算滚动窗口剩余(基于 history 滑窗)。

    真实逻辑:
    - now = state.last_updated(若为 0 则用 history 末位或 0)
    - cutoff = now - window_seconds
    - used_in_window = sum(history 中 ts >= cutoff 的 tokens)
    - remaining = limit - used_in_window
    """
    if window_name not in state.windows:
        raise KeyError(f"window {window_name!r} not in state")
    w = state.windows[window_name]
    now = state.last_updated
    if now <= 0 and w.used_history:
        now = w.used_history[-1][0]
    cutoff = now - w.window_seconds
    used_in_window = sum(tk for ts, tk in w.used_history if ts >= cutoff)
    return w.limit_tokens - used_in_window


def prune_all(state: QuotaState, at: float | None = None) -> None:
    """对所有窗口做 prune + recompute(单独可调用,维护用)。"""
    now = at if at is not None else state.last_updated
    if now < 0:
        raise ValueError(f"at must be >= 0, got {now}")
    for w in state.windows.values():
        _prune_history(w, now)
        _recompute_used_from_history(w, now)
    state.last_updated = float(now)


# ============ Presets ============

def make_default_state(
    limit_5h: int = 100_000,
    limit_weekly: int = 1_000_000,
    limit_monthly: int = 4_000_000,
    at: float = 0.0,
) -> QuotaState:
    """构造一个标准 3 窗口 QuotaState(测试 / 启动用)。"""
    return QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=limit_5h),
            "weekly": QuotaWindow(name="weekly", limit_tokens=limit_weekly),
            "monthly": QuotaWindow(name="monthly", limit_tokens=limit_monthly),
        },
        last_updated=at,
    )


__all__ = [
    "WINDOW_5H_SECONDS",
    "WINDOW_WEEKLY_SECONDS",
    "WINDOW_MONTHLY_SECONDS",
    "WINDOW_DURATIONS",
    "VALID_WINDOW_NAMES",
    "QuotaWindow",
    "QuotaState",
    "record_usage",
    "check_available",
    "eta_exhaustion",
    "would_exceed_within",
    "rolling_remaining",
    "prune_all",
    "make_default_state",
]
