"""convergent_detector 单元测试 (15 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations
import pytest
import sys
from pathlib import Path

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.convergent_detector import (
    Idea,
    Proposal,
    ConvergentIdea,
    ConflictPair,
    extract_ideas,
    detect_convergent,
    detect_conflicting,
    arbitrate_conflicts,
    convergent_summary,
    STOPWORDS,
    JACCARD_THRESHOLD,
)


# ============ 辅助: 构造 Proposal ============

def make_proposal(idx: int, author: str, text: str) -> Proposal:
    """从文本自动抽取 ideas 构造 Proposal"""
    ideas = extract_ideas(text, idx)
    return Proposal(proposal_idx=idx, author=author, text=text, ideas=ideas)


# ============ extract_ideas 测试 ============

def test_extract_ideas_split_sentences():
    """单 proposal 拆句: 句号/分号/换行切分"""
    text = "We should add caching layer. Performance will improve significantly. Use Redis for storage."
    ideas = extract_ideas(text, 0)
    # 3 个完整句, 都 >= 4 词
    assert len(ideas) >= 3, f"expected >=3 ideas, got {len(ideas)}"
    for idea in ideas:
        assert isinstance(idea, Idea)
        assert idea.source_proposal_idx == 0
        assert len(idea.keywords) > 0


def test_extract_ideas_filter_short():
    """过滤 < 4 词的短句"""
    text = "Hi. We should add caching layer for performance improvements today. OK."
    ideas = extract_ideas(text, 0)
    # "Hi." 和 "OK." 都 < 4 词,应被过滤
    for idea in ideas:
        assert len(idea.keywords) >= 1  # 至少有关键词
    # "Hi" / "OK" 不在结果中
    idea_texts = [i.text for i in ideas]
    assert "Hi" not in idea_texts
    assert "OK" not in idea_texts


def test_extract_ideas_keyword_normalization():
    """关键词归一化: 大写转小写 + 停用词过滤"""
    text = "The CACHING Layer should Improve Performance For The Application."
    ideas = extract_ideas(text, 0)
    assert len(ideas) >= 1
    idea = ideas[0]
    kws = idea.keywords
    # 全小写
    assert all(k == k.lower() for k in kws)
    # 无停用词
    assert "the" not in kws
    assert "for" not in kws
    # 保留核心词
    assert "caching" in kws
    assert "layer" in kws
    assert "improve" in kws
    assert "performance" in kws


# ============ detect_convergent 测试 ============

def test_detect_convergent_3_of_5():
    """3 个 proposal 共享 1 想法 → CONVERGENT, strength 0.6"""
    shared = "We should add a caching layer to improve performance."
    p0 = make_proposal(0, "alice", f"{shared} Also consider Redis storage.")
    p1 = make_proposal(1, "bob", f"{shared} Database queries are slow.")
    p2 = make_proposal(2, "carol", f"{shared} Cache invalidation is hard.")
    p3 = make_proposal(3, "dave", "Use a message queue for async tasks.")
    p4 = make_proposal(4, "eve", "Try microservices architecture instead.")

    convergent = detect_convergent([p0, p1, p2, p3, p4], min_support=3)
    assert len(convergent) >= 1
    # 找到 caching 相关的
    caching = [c for c in convergent if "caching" in c.canonical_text.lower() or "caching" in c.exact_quote.lower()]
    assert len(caching) == 1
    c = caching[0]
    assert c.strength == 0.6, f"expected 0.6 (3/5), got {c.strength}"
    assert set(c.supporting_proposals) == {0, 1, 2}


def test_detect_convergent_below_min_support():
    """2 proposal 共享 → 不达 min_support=3"""
    shared = "We should add a caching layer to improve performance."
    p0 = make_proposal(0, "alice", f"{shared} Also consider Redis storage.")
    p1 = make_proposal(1, "bob", f"{shared} Database queries are slow.")
    p2 = make_proposal(2, "carol", "Use a message queue for async tasks.")
    p3 = make_proposal(3, "dave", "Try microservices architecture instead.")
    p4 = make_proposal(4, "eve", "Add rate limiting to the API endpoints.")

    convergent = detect_convergent([p0, p1, p2, p3, p4], min_support=3)
    # caching 只在 2 个 proposal 出现 → 不应出现
    caching = [c for c in convergent if "caching" in c.canonical_text.lower()]
    assert len(caching) == 0, f"expected no caching convergent, got {caching}"


def test_detect_convergent_min_support_2():
    """min_support=2 改 2 → 触发 (2 个 proposal 共享)"""
    shared = "We should add a caching layer to improve performance."
    p0 = make_proposal(0, "alice", f"{shared} Also consider Redis storage.")
    p1 = make_proposal(1, "bob", f"{shared} Database queries are slow.")
    p2 = make_proposal(2, "carol", "Use a message queue for async tasks.")

    convergent = detect_convergent([p0, p1, p2], min_support=2)
    caching = [c for c in convergent if "caching" in c.canonical_text.lower()]
    assert len(caching) == 1
    c = caching[0]
    assert c.strength == round(2 / 3, 4)
    assert set(c.supporting_proposals) == {0, 1}


def test_detect_convergent_all_5():
    """5 proposal 全共享 → strength 1.0"""
    shared = "We should add a caching layer to improve performance."
    proposals = []
    for i in range(5):
        text = f"{shared} Additional note number {i} here."
        proposals.append(make_proposal(i, f"user_{i}", text))

    convergent = detect_convergent(proposals, min_support=3)
    caching = [c for c in convergent if "caching" in c.canonical_text.lower()]
    assert len(caching) == 1
    assert caching[0].strength == 1.0
    assert set(caching[0].supporting_proposals) == {0, 1, 2, 3, 4}


def test_detect_convergent_canonical_longest_quote():
    """合并: 取最长 quote 作 canonical_text"""
    base = "We should add caching"
    long = "We should add a comprehensive caching layer to dramatically improve performance"
    p0 = make_proposal(0, "alice", f"{long}. Some extra context follows here.")
    p1 = make_proposal(1, "bob", f"{base}. More discussion about it now.")
    p2 = make_proposal(2, "carol", f"{long}. This is the second version.")

    convergent = detect_convergent([p0, p1, p2], min_support=2)
    caching = [c for c in convergent if "caching" in c.canonical_text.lower()]
    assert len(caching) == 1
    c = caching[0]
    # canonical_text 应是最长的 long (句号被切分时剥离)
    assert c.canonical_text == long, f"expected longest quote, got {c.canonical_text!r}"
    assert c.exact_quote == long
    # 应包含 supporting {0, 2} (用了 long quote 的两个)
    assert set(c.supporting_proposals) == {0, 2}


# ============ detect_conflicting 测试 ============

def test_detect_conflicting_use_vs_avoid():
    """'use Redis' vs 'avoid Redis' → 冲突"""
    p0 = make_proposal(0, "alice", "We should use Redis for caching. It is fast.")
    p1 = make_proposal(1, "bob", "We should avoid Redis for caching. It is complex.")
    p2 = make_proposal(2, "carol", "Database is fine. No caching needed here.")

    conflicts = detect_conflicting([p0, p1, p2])
    assert len(conflicts) >= 1
    redis_conflict = [c for c in conflicts if "redis" in c.option_a or "redis" in c.option_b]
    assert len(redis_conflict) == 1
    c = redis_conflict[0]
    # 一边 use redis, 一边 avoid redis
    has_use = "use redis" in c.option_a or "use redis" in c.option_b
    has_avoid = "avoid redis" in c.option_a or "avoid redis" in c.option_b
    assert has_use and has_avoid
    assert 0 in c.supporting_a + c.supporting_b
    assert 1 in c.supporting_a + c.supporting_b


def test_detect_conflicting_should_vs_should_not():
    """'should do A' vs 'should not do A' → 冲突"""
    p0 = make_proposal(0, "alice", "We should migrate to microservices soon.")
    p1 = make_proposal(1, "bob", "We should not migrate to microservices now.")
    p2 = make_proposal(2, "carol", "The monolith is fine for current scale.")

    conflicts = detect_conflicting([p0, p1, p2])
    assert len(conflicts) >= 1
    migrate = [c for c in conflicts if "migrate" in c.option_a and "migrate" in c.option_b]
    assert len(migrate) == 1
    c = migrate[0]
    has_should = "should migrate" in c.option_a or "should migrate" in c.option_b
    has_should_not = "should not migrate" in c.option_a or "should not migrate" in c.option_b
    assert has_should and has_should_not


def test_detect_conflicting_no_conflict():
    """无冲突情况 — 所有 proposal 一边倒"""
    p0 = make_proposal(0, "alice", "We should use Redis for caching.")
    p1 = make_proposal(1, "bob", "We should use Redis for sessions too.")
    p2 = make_proposal(2, "carol", "Redis is the right choice for performance.")

    conflicts = detect_conflicting([p0, p1, p2])
    # 全部支持 use redis, 无 avoid redis → redis 不冲突
    redis_conflict = [c for c in conflicts if "redis" in c.option_a or "redis" in c.option_b]
    assert len(redis_conflict) == 0


# ============ arbitrate_conflicts 测试 ============

def test_arbitrate_conflicts_pick_higher_viability():
    """选 viability 高者"""
    p0 = make_proposal(0, "alice", "We should use Redis for caching. It is fast.")
    p1 = make_proposal(1, "bob", "We should avoid Redis for caching. Too complex.")
    p2 = make_proposal(2, "carol", "We should use Memcached for caching. Simpler.")

    conflicts = detect_conflicting([p0, p1, p2])
    # viability: alice=0.9, bob=0.3, carol=0.7
    # option_a = use redis (alice 0.9, carol 0.7), option_b = avoid redis (bob 0.3)
    # A 优势 → winner = A
    viability = {0: 0.9, 1: 0.3, 2: 0.7}
    results = arbitrate_conflicts(conflicts, viability)
    # 找到 redis 冲突
    redis_result = None
    for conflict, winner, conf in results:
        if "redis" in conflict.option_a or "redis" in conflict.option_b:
            redis_result = (conflict, winner, conf)
            break
    assert redis_result is not None
    _, winner, confidence = redis_result
    assert winner == "A", f"expected winner=A (use redis), got {winner}"
    assert 0.0 < confidence <= 1.0


def test_arbitrate_conflicts_multiple_independent():
    """多个冲突独立仲裁"""
    p0 = make_proposal(0, "alice", "We should use Redis. We should use Docker too.")
    p1 = make_proposal(1, "bob", "We should avoid Redis. We should avoid Docker as well.")
    p2 = make_proposal(2, "carol", "We should use Postgres. We should use Kubernetes.")
    p3 = make_proposal(3, "dave", "We should not use Postgres. We should not use Kubernetes.")

    conflicts = detect_conflicting([p0, p1, p2, p3])
    assert len(conflicts) >= 2
    # 仲裁: alice+carol vs bob+dave
    viability = {0: 0.9, 1: 0.4, 2: 0.8, 3: 0.3}
    results = arbitrate_conflicts(conflicts, viability)
    assert len(results) == len(conflicts)
    # 每个结果都是 (ConflictPair, "A"/"B", float)
    for conflict, winner, confidence in results:
        assert winner in ("A", "B")
        assert 0.0 <= confidence <= 1.0


# ============ convergent_summary 测试 ============

def test_summary_diversity_zero_when_all_convergent():
    """diversity_score = 0 当全 convergent"""
    shared = "We should add a comprehensive caching layer to improve performance."
    proposals = []
    for i in range(5):
        text = f"{shared} Additional note number {i} here for context."
        proposals.append(make_proposal(i, f"user_{i}", text))

    summary = convergent_summary(proposals, min_support=3)
    assert summary["diversity_score"] == 0.0, f"expected 0.0, got {summary['diversity_score']}"
    assert len(summary["convergent"]) >= 1
    assert summary["proposal_count"] == 5
    assert summary["total_ideas"] >= 5


def test_summary_diversity_high_when_heterogeneous():
    """diversity_score 高当全异质"""
    proposals = [
        make_proposal(0, "alice", "We should add a comprehensive caching layer to improve performance."),
        make_proposal(1, "bob", "Database queries need optimization for speed."),
        make_proposal(2, "carol", "Microservices architecture enables independent deployment."),
        make_proposal(3, "dave", "Rate limiting prevents abuse and protects the system."),
        make_proposal(4, "eve", "Message queue decouples producers from consumers."),
    ]

    summary = convergent_summary(proposals, min_support=3)
    # 5 个完全不同的想法 → 0 convergent → diversity = 1.0
    assert summary["diversity_score"] == 1.0, f"expected 1.0, got {summary['diversity_score']}"
    assert len(summary["convergent"]) == 0


def test_summary_includes_conflicts():
    """summary 包含 conflicts 字段"""
    p0 = make_proposal(0, "alice", "We should use Redis for caching.")
    p1 = make_proposal(1, "bob", "We should avoid Redis for caching.")

    summary = convergent_summary([p0, p1], min_support=2)
    assert "convergent" in summary
    assert "conflicts" in summary
    assert "total_ideas" in summary
    assert "diversity_score" in summary
    # 应有 redis 冲突
    redis_conflict = [c for c in summary["conflicts"] if "redis" in c["option_a"] or "redis" in c["option_b"]]
    assert len(redis_conflict) >= 1
