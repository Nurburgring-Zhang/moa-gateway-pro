"""versioning — 方案版本化 (来自 03 MoA-Engine) + LLM-as-Judge 单答评分 (来自 02 MoA-together-ai)

核心能力:
  1. ProposalVersion: 单个方案版本 (v1 / v2 / ...) 的元数据
  2. VersionChain: 同一 proposal 的版本链 (按时间顺序)
  3. VersionStore: 多 proposal 维度管理版本链, add_version / get_chain / get_version / latest
  4. LLM-as-Judge 单答评分 (parse_rating): 多种格式 → 1-10 整数 (与 quorum 共用语义, 失败回退 5)
  5. LLM-as-Judge 对战 (parse_battle): 解析 A/B/tie + confidence
  6. swap_positions_battle: 抗位置偏置双向对战
  7. diff_versions: 版本间差异分析 (长度/关键词/质量评分)

设计原则:
  - version_id 自动生成 v1 / v2 / v3 ... 顺序, 同一 proposal 内单调递增
  - parent_version_id 指向上一版本, None 表示首版
  - critique / improvement_summary 是元数据, 用于"为什么改"的审计
  - parse_rating / parse_battle / swap_positions_battle 与 quorum 语义一致
  - diff_versions 返回 dict: len_change / added_keywords / removed_keywords / score_delta
"""
from __future__ import annotations

import re
import json
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Callable, Dict, Any, Set


# ============ 数据模型 ============
@dataclass
class ProposalVersion:
    """单个方案版本"""
    version_id: str  # "v1", "v2", ...
    content: str
    parent_version_id: Optional[str] = None
    created_at: float = 0.0
    created_by: str = "system"
    critique: Optional[str] = None
    improvement_summary: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class VersionChain:
    """同一 proposal 的版本链 (按时间顺序)"""
    proposal_id: str
    versions: List[ProposalVersion] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "proposal_id": self.proposal_id,
            "versions": [v.to_dict() for v in self.versions],
        }

    def __len__(self) -> int:
        return len(self.versions)


# ============ 评分正则 (与 quorum 对齐) ============
_RATING_PATTERNS = [
    re.compile(r"\[\[\s*rating[_\s:]*a\s*\]\]\s*[:\s]*([0-9]+)", re.IGNORECASE),
    re.compile(r"\[\[\s*rating\s*[:\s]\s*([0-9]+)\s*\]\]", re.IGNORECASE),
    re.compile(r"rating\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"score\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"\brate\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"\bgrade\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"\[([0-9]+)\s*/\s*10\]"),
    re.compile(r"([0-9]+)\s*/\s*10\b"),
]
_DEFAULT_RATING = 5


def parse_rating(judge_response: str) -> int:
    """从 judge 响应中提取 1-10 评分, 失败回退 5

    支持格式示例:
        "[[rating_a]] 8"          → 8
        "[[rating:7]]"            → 7
        "Rating: 9"               → 9
        "I would rate this 6/10"  → 6
        "Score = 4"               → 4
    """
    if not judge_response or not isinstance(judge_response, str):
        return _DEFAULT_RATING

    text = judge_response.strip()
    for pat in _RATING_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                val = int(m.group(1))
            except (ValueError, IndexError):
                continue
            if val < 1:
                val = 1
            elif val > 10:
                val = 10
            return val

    if re.search(r"\brating\b", text, re.IGNORECASE):
        nums = re.findall(r"-?\d+", text)
        if nums:
            try:
                val = int(nums[0])
                if val < 1:
                    val = 1
                elif val > 10:
                    val = 10
                return val
            except ValueError:
                pass
    return _DEFAULT_RATING


# ============ 对战解析 ============
_BATTLE_PATTERNS = [
    (re.compile(r"\[\[\s*winner\s*\]\]\s*[:\s]*([AB])\b", re.IGNORECASE), "explicit"),
    (re.compile(r"\bwinner\s*[:=\-]\s*([AB])\b", re.IGNORECASE), "explicit"),
    (re.compile(r"\bA\s+is\s+(?:better|superior|more\s+(?:helpful|accurate|relevant))\b", re.IGNORECASE), "A"),
    (re.compile(r"\bB\s+is\s+(?:better|superior|more\s+(?:helpful|accurate|relevant))\b", re.IGNORECASE), "B"),
    (re.compile(r"\bbetter\s+than\s+A\b", re.IGNORECASE), "B"),
    (re.compile(r"\bbetter\s+than\s+B\b", re.IGNORECASE), "A"),
    (re.compile(r"\bprefer\s+A\b", re.IGNORECASE), "A"),
    (re.compile(r"\bprefer\s+B\b", re.IGNORECASE), "B"),
    (re.compile(r"\b(?:I\s+)?choose\s+A\b", re.IGNORECASE), "A"),
    (re.compile(r"\b(?:I\s+)?choose\s+B\b", re.IGNORECASE), "B"),
    (re.compile(r"\b(tie|equal|draw|same\s+level|equivalent|neither)\b", re.IGNORECASE), "tie"),
]


def parse_battle(judge_response: str) -> Tuple[str, int]:
    """解析 judge 对战响应, 返回 (winner, confidence 0-1)

    winner ∈ {"A", "B", "tie"}
    confidence: 显式 winner 标签 / 强措辞 → 1, tie 措辞 → 0.5, 解析失败 → 0
    """
    if not judge_response or not isinstance(judge_response, str):
        return ("tie", 0)

    text = judge_response.strip()

    tie_m = re.search(r"\b(tie|equal|draw|same\s+level|equivalent|neither)\b", text, re.IGNORECASE)
    winner_m = None
    for idx, (pat, label) in enumerate(_BATTLE_PATTERNS):
        if label == "explicit":
            m = pat.search(text)
            if m:
                raw = m.group(1).upper()
                winner_m = ("B" if raw == "B" else "A", idx)
                break

    if winner_m is None:
        for idx, (pat, label) in enumerate(_BATTLE_PATTERNS):
            if label in ("A", "B"):
                if pat.search(text):
                    winner_m = (label, idx)
                    break

    if winner_m is None:
        if tie_m:
            return ("tie", 1)
        return ("tie", 0)

    winner = winner_m[0]
    if tie_m:
        return (winner, 0)
    return (winner, 1)


def swap_positions_battle(
    response_a: str,
    response_b: str,
    judge_fn: Callable[[str, str], str],
) -> str:
    """抗位置偏置双向对战

    算法:
    - 第 1 轮: judge_fn(response_a, response_b) → winner 标签
    - 第 2 轮: judge_fn(response_b, response_a) → 位置交换后重评
    - 两轮都指认同一个原始 response → 返回该 response
    - 不一致 → "tie"
    - 一致但都 tie → "tie"
    """
    raw1 = judge_fn(response_a, response_b)
    raw2 = judge_fn(response_b, response_a)

    w1, _c1 = parse_battle(raw1)
    w2, _c2 = parse_battle(raw2)

    if w1 == "A":
        first_winner = response_a
    elif w1 == "B":
        first_winner = response_b
    else:
        first_winner = "tie"

    if w2 == "A":
        second_winner = response_b
    elif w2 == "B":
        second_winner = response_a
    else:
        second_winner = "tie"

    if first_winner == "tie" and second_winner == "tie":
        return "tie"
    if first_winner == second_winner and first_winner != "tie":
        return first_winner
    return "tie"


# ============ Version Store ============
class VersionStore:
    """多 proposal 维度管理版本链

    用法:
        store = VersionStore()
        v1 = store.add_version("prop1", "first content")          # → v1
        v2 = store.add_version("prop1", "second content",
                               parent=v1, critique="too short",
                               improvement="add detail")           # → v2
        chain = store.get_chain("prop1")                          # [v1, v2]
        latest = store.latest("prop1")                            # v2
    """

    # 停用词: 不计入"关键词"统计的常见词
    _STOPWORDS: Set[str] = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "of", "in", "on", "at", "to", "for", "with", "by", "from",
        "and", "or", "but", "not", "no", "yes", "this", "that",
        "it", "its", "as", "if", "then", "than", "so", "do", "does",
        "did", "have", "has", "had", "i", "you", "we", "they",
        "he", "she", "him", "her", "his", "their", "our", "your",
        "my", "me", "us", "them", "will", "would", "should", "could",
        "can", "may", "might", "must", "shall",
    }

    def __init__(self) -> None:
        # proposal_id → VersionChain
        self._chains: Dict[str, VersionChain] = {}

    def add_version(
        self,
        proposal_id: str,
        content: str,
        parent: Optional[str] = None,
        critique: Optional[str] = None,
        improvement: Optional[str] = None,
        created_by: str = "system",
        created_at: Optional[float] = None,
    ) -> str:
        """添加新版本, 返回 version_id (e.g. "v1")

        参数:
            proposal_id: 方案 id (e.g. "prop1")
            content: 版本内容
            parent: 父版本 version_id, None 表示首版
            critique: 对上一版本的批评 (元数据, 不会覆盖, 仅记录在 *新* 版本上)
            improvement: 改进点说明 (元数据, 记录在 *新* 版本上)
            created_by: 创建者标识
            created_at: 时间戳; None → 当前时间
        """
        if not proposal_id or not isinstance(proposal_id, str):
            raise ValueError("proposal_id must be a non-empty string")
        if not isinstance(content, str):
            raise ValueError("content must be a string")

        if proposal_id not in self._chains:
            self._chains[proposal_id] = VersionChain(proposal_id=proposal_id)

        chain = self._chains[proposal_id]
        seq = len(chain.versions) + 1
        version_id = f"v{seq}"

        ts = created_at if created_at is not None else time.time()

        version = ProposalVersion(
            version_id=version_id,
            content=content,
            parent_version_id=parent,
            created_at=ts,
            created_by=created_by,
            critique=critique,
            improvement_summary=improvement,
        )
        chain.versions.append(version)
        return version_id

    def get_chain(self, proposal_id: str) -> VersionChain:
        """获取版本链; 不存在则返回空链 (而非抛错)"""
        return self._chains.get(proposal_id, VersionChain(proposal_id=proposal_id))

    def get_version(self, proposal_id: str, version_id: str) -> Optional[ProposalVersion]:
        """获取指定版本; 不存在返回 None"""
        chain = self._chains.get(proposal_id)
        if not chain:
            return None
        for v in chain.versions:
            if v.version_id == version_id:
                return v
        return None

    def latest(self, proposal_id: str) -> Optional[ProposalVersion]:
        """获取最新版本; 不存在返回 None"""
        chain = self._chains.get(proposal_id)
        if not chain or not chain.versions:
            return None
        return chain.versions[-1]

    def all_proposal_ids(self) -> List[str]:
        return list(self._chains.keys())


# ============ diff_versions ============
def _extract_keywords(text: str, stopwords: Set[str]) -> Set[str]:
    """提取小写关键词 (去停用词, 长度 ≥ 3)"""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z\u4e00-\u9fff][a-zA-Z0-9\u4e00-\u9fff]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in stopwords}


def diff_versions(
    v1: ProposalVersion,
    v2: ProposalVersion,
    stopwords: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """对比两个版本的差异

    返回:
        {
            "v1_id": "v1",
            "v2_id": "v2",
            "len_v1": int,
            "len_v2": int,
            "len_delta": int (v2 - v1),
            "len_change_ratio": float (v2_len / v1_len, v1 为 0 时返回 0.0),
            "added_keywords": List[str]   # 排序后
            "removed_keywords": List[str]
            "common_keywords": List[str]
            "similarity": float            # Jaccard 0-1
            "score_v1": int               # parse_rating(critique) 兜底
            "score_v2": int
            "score_delta": int
        }

    设计:
        - v1 是 older, v2 是 newer (调用方保证顺序, 不做时间校验)
        - 关键词集合: Jaccard 相似度 = |∩| / |∪|
        - score 用 critique 字段: 若无 critique 视为 5 分 (中位)
    """
    if stopwords is None:
        stopwords = VersionStore._STOPWORDS

    len_v1 = len(v1.content)
    len_v2 = len(v2.content)
    len_delta = len_v2 - len_v1
    len_change_ratio = (len_v2 / len_v1) if len_v1 > 0 else 0.0

    kw_v1 = _extract_keywords(v1.content, stopwords)
    kw_v2 = _extract_keywords(v2.content, stopwords)

    added = kw_v2 - kw_v1
    removed = kw_v1 - kw_v2
    common = kw_v1 & kw_v2
    union = kw_v1 | kw_v2
    similarity = (len(common) / len(union)) if union else 0.0

    # score: 用 critique 字段作为评分锚; 无 critique 视为 5
    score_v1 = parse_rating(v1.critique) if v1.critique else 5
    score_v2 = parse_rating(v2.critique) if v2.critique else 5
    score_delta = score_v2 - score_v1

    return {
        "v1_id": v1.version_id,
        "v2_id": v2.version_id,
        "len_v1": len_v1,
        "len_v2": len_v2,
        "len_delta": len_delta,
        "len_change_ratio": round(len_change_ratio, 4),
        "added_keywords": sorted(added),
        "removed_keywords": sorted(removed),
        "common_keywords": sorted(common),
        "similarity": round(similarity, 4),
        "score_v1": score_v1,
        "score_v2": score_v2,
        "score_delta": score_delta,
    }


# ============ JSON 序列化 ============
def to_json(obj: Any) -> str:
    """统一 JSON 序列化 (支持 dataclass 嵌套)"""
    def _default(o: Any) -> Any:
        if hasattr(o, "to_dict"):
            return o.to_dict()
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        return str(o)

    return json.dumps(obj, default=_default, ensure_ascii=False, indent=2)


__all__ = [
    "ProposalVersion",
    "VersionChain",
    "VersionStore",
    "parse_rating",
    "parse_battle",
    "swap_positions_battle",
    "diff_versions",
    "to_json",
]
