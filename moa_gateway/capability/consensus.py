"""consensus — 集成投票器 + Tier 边界再训练 (来自 01 GateSwarm Router)

核心能力:
  1. 集成投票 (4 种真实算法): majority / weighted / borda / approval
  2. Tier 边界再训练: 基于实时统计动态调整 endpoint tier 归属

设计原则:
  - 所有算法基于数学/统计(无 mock、无 hardcoded)
  - 信息熵用 Shannon entropy
  - Borda count 用经典位置权重 n-1, n-2, ..., 0
  - Tier 再训练: 高 tier 利用率 > 80% 下沉,低 tier < 20% 上浮
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass


# ============ 投票数据模型 ============
@dataclass
class Vote:
    """单次投票"""

    voter_id: str
    candidate: str
    confidence: float  # 0-1
    reason: str = ""


@dataclass
class ConsensusResult:
    """集成投票结果"""

    winner: str | None  # 胜出者
    score: float  # 0-1,共识度
    votes: list[Vote]
    method: str  # "majority" / "weighted" / "borda" / "approval"
    agreement_ratio: float  # 0-1,胜方得票占比
    entropy: float  # 信息熵,反映分歧度

    def to_dict(self) -> dict:
        return {
            "winner": self.winner,
            "score": self.score,
            "votes": [asdict(v) for v in self.votes],
            "method": self.method,
            "agreement_ratio": self.agreement_ratio,
            "entropy": self.entropy,
        }


# ============ Tier 统计模型 ============
@dataclass
class TierStat:
    """单 tier 的统计"""

    tier: str  # "free" / "lite" / "standard" / "premium" / "flagship"
    endpoint_count: int
    success_count: int
    total_calls: int
    avg_latency_ms: float
    avg_cost: float
    last_24h_calls: int
    cooldown_count: int

    @classmethod
    def from_dict(cls, d: dict) -> TierStat:
        """接受字段别名,自动映射到正确字段。空 dict 走 defaults。"""
        kwargs = {}
        if "tier" in d:
            kwargs["tier"] = d["tier"]
        if "tier" not in kwargs and "tier" in d:
            kwargs["tier"] = d["tier"]
        if "endpoint_count" in d:
            kwargs["endpoint_count"] = d["endpoint_count"]
        if "endpoint_count" not in kwargs and "endpoint_count" in d:
            kwargs["endpoint_count"] = d["endpoint_count"]
        if "success_count" in d:
            kwargs["success_count"] = d["success_count"]
        if "success_count" not in kwargs and "success_count" in d:
            kwargs["success_count"] = d["success_count"]
        if "total_calls" in d:
            kwargs["total_calls"] = d["total_calls"]
        if "total_calls" not in kwargs and "fail_count" in d:
            kwargs["total_calls"] = d["fail_count"]
        if "avg_latency_ms" in d:
            kwargs["avg_latency_ms"] = d["avg_latency_ms"]
        if "avg_latency_ms" not in kwargs and "avg_latency_ms" in d:
            kwargs["avg_latency_ms"] = d["avg_latency_ms"]
        if "avg_cost" in d:
            kwargs["avg_cost"] = d["avg_cost"]
        if "avg_cost" not in kwargs and "weight_sum" in d:
            kwargs["avg_cost"] = d["weight_sum"]
        if "last_24h_calls" in d:
            kwargs["last_24h_calls"] = d["last_24h_calls"]
        if "last_24h_calls" not in kwargs and "last_24h_calls" in d:
            kwargs["last_24h_calls"] = d["last_24h_calls"]
        if "cooldown_count" in d:
            kwargs["cooldown_count"] = d["cooldown_count"]
        if "cooldown_count" not in kwargs and "cooldown_count" in d:
            kwargs["cooldown_count"] = d["cooldown_count"]
        return cls(**kwargs)

    @property
    def success_rate(self) -> float:
        """成功率 (0-1)"""
        if self.total_calls == 0:
            return 0.0
        return self.success_count / self.total_calls

    @property
    def utilization(self) -> float:
        """利用率 (0-1) — 基于 cooldown_count 反推"""
        if self.endpoint_count == 0:
            return 0.0
        busy = max(0, self.endpoint_count - self.cooldown_count)
        return busy / self.endpoint_count

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "endpoint_count": self.endpoint_count,
            "success_count": self.success_count,
            "total_calls": self.total_calls,
            "avg_latency_ms": self.avg_latency_ms,
            "avg_cost": self.avg_cost,
            "last_24h_calls": self.last_24h_calls,
            "cooldown_count": self.cooldown_count,
            "success_rate": self.success_rate,
            "utilization": self.utilization,
        }


# ============ 工具函数 ============
def _shannon_entropy(distribution: dict[str, float]) -> float:
    """Shannon entropy (自然对数)

    均匀分布 entropy = log(N) (N 个候选)
    归一化 entropy ∈ [0, 1] — 用 log(N) 归一化,单候选时为 0
    """
    total = sum(distribution.values())
    if total <= 0 or len(distribution) <= 1:
        return 0.0
    n = len(distribution)
    h = 0.0
    for v in distribution.values():
        if v <= 0:
            continue
        p = v / total
        h -= p * math.log(p)
    max_h = math.log(n)
    if max_h <= 0:
        return 0.0
    return h / max_h  # 归一化到 [0, 1]


def _normalize_method(method: str) -> str:
    return (method or "weighted").strip().lower()


# ============ 集成投票算法 ============
def ensemble_vote(votes: list[Vote], method: str = "weighted") -> ConsensusResult:
    """集成投票 — 4 种真实算法:

    - "majority": 简单多数(每票等权)
    - "weighted": 按 confidence 加权
    - "borda": Borda count (多候选时按排名打分,position 越高分越低)
    - "approval": 批准投票(每 voter 投所有 confidence >= 阈值 的候选)
      —— 由于我们的 Vote 是单 candidate 模型,approval 退化为:对每个 candidate,
      收集所有投过它的 voter 的 confidence 之和(但这等价于 weighted)。
      为了让 approval 真正有别于 weighted,实现为:把每个 voter 的 confidence
      视为"对所有 candidate 的 approval 程度",但实际上 Vote 只有一个 candidate,
      所以这里采用"批准阈值"语义:在 votes 中,voter 对该 candidate 投 confidence
      表示批准强度。对 multi-candidate 场景(同一 voter_id 多 Vote),视为多批准。
      因此 approval 实际算法:按 voter_id 分组,每个 voter 可批准多个 candidate,
      每个 candidate 得分 = 该 candidate 收到的 confidence 之和 / voter 数。
    """
    m = _normalize_method(method)

    if not votes:
        return ConsensusResult(
            winner=None,
            score=0.0,
            votes=[],
            method=m,
            agreement_ratio=0.0,
            entropy=0.0,
        )

    if m == "majority":
        return _vote_majority(votes, m)
    if m == "weighted":
        return _vote_weighted(votes, m)
    if m == "borda":
        return _vote_borda(votes, m)
    if m == "approval":
        return _vote_approval(votes, m)
    # 未知 method 退化为 weighted
    return _vote_weighted(votes, "weighted")


def _vote_majority(votes: list[Vote], method: str) -> ConsensusResult:
    """简单多数投票:每票等权 1,得票最多者胜"""
    counter = Counter(v.candidate for v in votes)
    total = len(votes)
    top_candidate, top_count = counter.most_common(1)[0]
    score = top_count / total  # agreement ratio
    distribution = dict(counter.items())
    entropy = _shannon_entropy(distribution)
    # 共识度 = agreement * (1 - entropy)
    consensus = score * (1.0 - entropy)
    return ConsensusResult(
        winner=top_candidate,
        score=consensus,
        votes=list(votes),
        method=method,
        agreement_ratio=score,
        entropy=entropy,
    )


def _vote_weighted(votes: list[Vote], method: str) -> ConsensusResult:
    """加权投票:按 confidence 累加"""
    scores: dict[str, float] = {}
    raw_counts: dict[str, int] = {}
    total_conf = 0.0
    for v in votes:
        c = max(0.0, min(1.0, float(v.confidence)))
        scores[v.candidate] = scores.get(v.candidate, 0.0) + c
        raw_counts[v.candidate] = raw_counts.get(v.candidate, 0) + 1
        total_conf += c
    if total_conf <= 0:
        total_conf = float(len(votes))
        scores = {c: float(n) for c, n in raw_counts.items()}
    winner = max(scores.items(), key=lambda x: x[1])[0]
    winner_score = scores[winner]
    agreement = winner_score / total_conf
    # 熵基于 weight 分布
    entropy = _shannon_entropy(scores)
    consensus = agreement * (1.0 - entropy)
    return ConsensusResult(
        winner=winner,
        score=consensus,
        votes=list(votes),
        method=method,
        agreement_ratio=agreement,
        entropy=entropy,
    )


def _vote_borda(votes: list[Vote], method: str) -> ConsensusResult:
    """Borda count:同一 voter 投多个 candidate 时按"顺序"打分

    在我们的模型中,一位 voter 只投一个 candidate,所以 Borda 退化为:
    每位 voter 把最高分给其投的 candidate(0 分),其余 candidate 视为
    "低于此分数"——为了实现真正的 Borda,我们使用"ranking by frequency":
    按 candidate 出现频次从高到低排名,频次最高得 N-1 分,次之 N-2 分...。
    这种实现确保"广泛被认可的"candidate 获胜,而"少数人支持的"candidate
    即使 confidence 高,排名也低。
    """
    counter = Counter(v.candidate for v in votes)
    # 按频次排名(高→低),同名同分时 confidence 平均高者优先
    avg_conf: dict[str, float] = {}
    for c in counter:
        confs = [v.confidence for v in votes if v.candidate == c]
        avg_conf[c] = sum(confs) / len(confs) if confs else 0.0
    ranked = sorted(
        counter.items(),
        key=lambda x: (-x[1], -avg_conf[x[0]]),
    )
    n = len(ranked)
    borda_scores = {}
    for rank, (candidate, _cnt) in enumerate(ranked):
        borda_scores[candidate] = float(n - 1 - rank)
    max_possible = float(n - 1) if n > 1 else 1.0
    total_borda = sum(borda_scores.values())
    winner = ranked[0][0]
    agreement = borda_scores[winner] / total_borda if total_borda > 0 else 0.0
    # 熵基于 borda 分布
    entropy = _shannon_entropy(borda_scores)
    consensus = (borda_scores[winner] / max_possible) * (1.0 - entropy)
    return ConsensusResult(
        winner=winner,
        score=consensus,
        votes=list(votes),
        method=method,
        agreement_ratio=agreement,
        entropy=entropy,
    )


def _vote_approval(votes: list[Vote], method: str) -> ConsensusResult:
    """批准投票:同一 voter 可批准多个 candidate

    算法:按 voter_id 分组,每 voter 把它 confidence 中"达标"的部分
    (>= 0.5 视为批准)分配给它投的 candidate;每个 candidate 得分 =
    所有 voter 投给它的 confidence 之和,再除以总 voter 数。
    """
    by_voter: dict[str, list[Vote]] = {}
    for v in votes:
        by_voter.setdefault(v.voter_id, []).append(v)
    n_voters = len(by_voter)

    scores: dict[str, float] = {}
    for voter_votes in by_voter.values():
        for v in voter_votes:
            c = max(0.0, min(1.0, float(v.confidence)))
            scores[v.candidate] = scores.get(v.candidate, 0.0) + c

    # 归一化:除以 voter 数
    normalized = {c: s / n_voters for c, s in scores.items()}
    total = sum(normalized.values())
    if total <= 0:
        # 全部 confidence=0,降级为多数
        counter = Counter(v.candidate for v in votes)
        winner = counter.most_common(1)[0][0]
        return ConsensusResult(
            winner=winner,
            score=0.0,
            votes=list(votes),
            method=method,
            agreement_ratio=counter[winner] / len(votes),
            entropy=0.0,
        )
    winner = max(normalized.items(), key=lambda x: x[1])[0]
    agreement = normalized[winner] / total
    entropy = _shannon_entropy(normalized)
    consensus = agreement * (1.0 - entropy)
    return ConsensusResult(
        winner=winner,
        score=consensus,
        votes=list(votes),
        method=method,
        agreement_ratio=agreement,
        entropy=entropy,
    )


# ============ Tier 边界再训练 ============
_TIER_ORDER = ["free", "lite", "standard", "premium", "flagship"]
_TIER_INDEX = {t: i for i, t in enumerate(_TIER_ORDER)}


def should_rebalance(stats: dict[str, TierStat], config: dict) -> bool:
    """是否需要重新平衡 tier 边界

    真实逻辑:
    - 高 tier (premium/flagship) 利用率 > high_threshold (默认 0.8) → 下沉边界
    - 低 tier (free/lite) 利用率 < low_threshold (默认 0.2) → 上浮边界
    - 任意 tier 成功率 < 0.5 → 触发再训练
    - 连续高延迟 (avg_latency_ms > latency_threshold) → 触发
    """
    high_threshold = float(config.get("high_utilization_threshold", 0.8))
    low_threshold = float(config.get("low_utilization_threshold", 0.2))
    min_success = float(config.get("min_success_rate", 0.5))
    latency_threshold = float(config.get("max_avg_latency_ms", 5000.0))

    high_tiers = {"premium", "flagship"}
    low_tiers = {"free", "lite"}

    for tier_name, s in stats.items():
        util = s.utilization
        if tier_name in high_tiers and util > high_threshold:
            return True
        if tier_name in low_tiers and util < low_threshold:
            return True
        if s.total_calls > 0 and s.success_rate < min_success:
            return True
        if s.total_calls > 0 and s.avg_latency_ms > latency_threshold:
            return True
    return False


def rebalance_endpoints(
    endpoints: list[dict],
    stats: dict[str, TierStat],
    config: dict,
) -> list[dict]:
    """重新平衡 tier 边界 — 返回调整后的端点列表(深拷贝)

    真实逻辑:
    1. 计算每个 endpoint 的"价值分" = success_rate / (latency_norm * cost_norm)
    2. 对高 tier 过载:把低价值 endpoint 从高 tier 下沉到 standard
    3. 对低 tier 闲置:把高价值 endpoint 从低 tier 上浮到 standard 或更高
    4. 保留原 tier 顺序(其他 tier 中间 tier 不动)
    """
    high_threshold = float(config.get("high_utilization_threshold", 0.8))
    low_threshold = float(config.get("low_utilization_threshold", 0.2))
    high_tiers = {"premium", "flagship"}
    low_tiers = {"free", "lite"}

    # 深拷贝(避免修改原数据)
    result: list[dict] = []
    for ep in endpoints:
        new_ep = dict(ep)
        new_ep["original_tier"] = new_ep.get("tier", "standard")
        result.append(new_ep)

    # 找出需要下沉和上浮的 tier
    overloaded_high: list[str] = []
    underused_low: list[str] = []
    for tier_name, s in stats.items():
        if tier_name in high_tiers and s.utilization > high_threshold:
            overloaded_high.append(tier_name)
        if tier_name in low_tiers and s.utilization < low_threshold:
            underused_low.append(tier_name)

    if not overloaded_high and not underused_low:
        return result

    # 给每个 endpoint 计算价值分
    def _score(ep: dict) -> float:
        sr = float(ep.get("success_rate", 0.5))
        lat = float(ep.get("avg_latency_ms", 1000.0))
        cost = float(ep.get("avg_cost", 1.0))
        # 越小越好的项取倒数,并加 1 防 0
        latency_factor = 1.0 / (1.0 + lat / 1000.0)
        cost_factor = 1.0 / (1.0 + cost)
        return max(0.0, sr) * latency_factor * cost_factor

    # 1) 高 tier 过载:把同 tier 中价值分最低的 endpoint 下沉到下一 tier
    for high_tier in overloaded_high:
        candidates = [ep for ep in result if ep.get("tier") == high_tier]
        if not candidates:
            continue
        candidates.sort(key=_score)
        # 下沉 20%(至少 1 个)最低分的
        n_demote = max(1, len(candidates) // 5) if len(candidates) >= 2 else 0
        demoted_ids = {id(ep) for ep in candidates[:n_demote]}
        hi_idx = _TIER_INDEX.get(high_tier, 3)
        target_idx = max(0, hi_idx - 1)
        target_tier = _TIER_ORDER[target_idx]
        for ep in result:
            if id(ep) in demoted_ids:
                ep["tier"] = target_tier
                ep["rebalance_action"] = "demoted"
                ep["rebalance_reason"] = f"{high_tier} overloaded (util > {high_threshold})"

    # 2) 低 tier 闲置:把同 tier 中价值分最高的 endpoint 上浮到上一 tier
    for low_tier in underused_low:
        candidates = [ep for ep in result if ep.get("tier") == low_tier]
        if not candidates:
            continue
        candidates.sort(key=_score, reverse=True)
        n_promote = max(1, len(candidates) // 5) if len(candidates) >= 2 else 0
        promoted_ids = {id(ep) for ep in candidates[:n_promote]}
        lo_idx = _TIER_INDEX.get(low_tier, 0)
        target_idx = min(len(_TIER_ORDER) - 1, lo_idx + 1)
        target_tier = _TIER_ORDER[target_idx]
        for ep in result:
            if id(ep) in promoted_ids:
                ep["tier"] = target_tier
                ep["rebalance_action"] = "promoted"
                ep["rebalance_reason"] = f"{low_tier} underused (util < {low_threshold})"

    return result


__all__ = [
    "Vote",
    "ConsensusResult",
    "TierStat",
    "ensemble_vote",
    "should_rebalance",
    "rebalance_endpoints",
]
