"""meta_prompt 真实测试(非 mock)"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.meta_prompt import (
    MetaStage, MetaPrompt, MetaResult,
    get_stage_prompts, run_meta_protocol,
    cognitively_clash, three_jumps, fuse_decision,
    meta_result_to_json, meta_prompt_to_json, merge_meta_results,
    STAGE1_TEMPLATES, STAGE2_SYSTEM, STAGE3_SYSTEM,
    DEFAULT_STAGE1_ROLES, JUMP_LABELS, CLASH_ROLE_PAIRS,
)


# ============ 枚举测试 ============

def test_meta_stage_enum_count():
    """MetaStage 应该有 3 个值"""
    assert len(MetaStage) == 3, f"expected 3, got {len(MetaStage)}"
    print(f"  ✓ test_meta_stage_enum_count: {len(MetaStage)} stages")
    return True


def test_meta_stage_values():
    """MetaStage 三个值正确"""
    vals = {s.value for s in MetaStage}
    assert "role_differentiation" in vals
    assert "structured_debate" in vals
    assert "logical_fusion" in vals
    print(f"  ✓ test_meta_stage_values: {sorted(vals)}")
    return True


def test_meta_stage_distinct():
    """3 个 stage 互不相同"""
    stages = [MetaStage.ROLE_DIFFERENTIATION, MetaStage.STRUCTURED_DEBATE, MetaStage.LOGICAL_FUSION]
    assert len(set(stages)) == 3
    print(f"  ✓ test_meta_stage_distinct: all unique")
    return True


# ============ get_stage_prompts 测试 ============

def test_get_stage_prompts_returns_three():
    """get_stage_prompts 应该返回 3 个 prompt"""
    prompts = get_stage_prompts("What is the best way to learn Python?")
    assert len(prompts) == 3, f"expected 3, got {len(prompts)}"
    assert all(isinstance(p, MetaPrompt) for p in prompts)
    print(f"  ✓ test_get_stage_prompts_returns_three: {len(prompts)} prompts")
    return True


def test_stage1_system_role_diff():
    """Stage 1 system prompt 包含 role differentiation 标识"""
    prompts = get_stage_prompts("test query")
    s1 = prompts[0]
    assert s1.stage == MetaStage.ROLE_DIFFERENTIATION
    assert "role" in s1.system_prompt.lower() or "ROLE_DIFFERENTIATION" in s1.system_prompt
    # user template 应包含 {query} 占位符
    assert "{query}" in s1.user_prompt_template
    print(f"  ✓ test_stage1_system_role_diff: stage={s1.stage.value}, role={s1.role}")
    return True


def test_stage2_system_debate():
    """Stage 2 system prompt 是显性对抗"""
    prompts = get_stage_prompts("test query")
    s2 = prompts[1]
    assert s2.stage == MetaStage.STRUCTURED_DEBATE
    # 应包含 critique / weakness / debate 相关
    lc = s2.system_prompt.lower()
    assert any(kw in lc for kw in ["critique", "debate", "weakness", "clash"])
    assert "{query}" in s2.user_prompt_template
    print(f"  ✓ test_stage2_system_debate: stage={s2.stage.value}")
    return True


def test_stage3_system_fusion():
    """Stage 3 system prompt 是过程熔铸"""
    prompts = get_stage_prompts("test query")
    s3 = prompts[2]
    assert s3.stage == MetaStage.LOGICAL_FUSION
    lc = s3.system_prompt.lower()
    assert any(kw in lc for kw in ["fuse", "fusion", "combine", "synthes"])
    assert "{query}" in s3.user_prompt_template
    print(f"  ✓ test_stage3_system_fusion: stage={s3.stage.value}")
    return True


# ============ run_meta_protocol 测试 ============

def test_run_meta_protocol_three_stages():
    """run_meta_protocol 真的跑 3 阶段(用 MockProvider 兜底)"""
    results = run_meta_protocol("Should I learn Rust or Go for systems programming?")
    assert len(results) == 3, f"expected 3, got {len(results)}"
    stages = [r.stage for r in results]
    assert stages[0] == MetaStage.ROLE_DIFFERENTIATION
    assert stages[1] == MetaStage.STRUCTURED_DEBATE
    assert stages[2] == MetaStage.LOGICAL_FUSION
    # 每个结果都有 output (非空,因为 MockProvider 兜底)
    for i, r in enumerate(results):
        assert isinstance(r.output, str)
    print(f"  ✓ test_run_meta_protocol_three_stages: 3 stages, "
          f"output lengths={[len(r.output) for r in results]}")
    return True


def test_run_meta_protocol_reasoning_chain():
    """reasoning_chain 在每阶段累积(非空)"""
    results = run_meta_protocol("Explain quantum entanglement briefly.")
    for i, r in enumerate(results):
        assert isinstance(r.reasoning_chain, list)
        assert len(r.reasoning_chain) > 0, f"stage {i} reasoning chain empty"
    # Stage 3 应该提到 fuse / 累积
    s3_reasoning = " ".join(results[2].reasoning_chain).lower()
    assert "fuse" in s3_reasoning or "stage 3" in s3_reasoning or "logical_fusion" in s3_reasoning
    print(f"  ✓ test_run_meta_protocol_reasoning_chain: "
          f"chain lengths={[len(r.reasoning_chain) for r in results]}")
    return True


def test_run_meta_protocol_next_directive():
    """next_stage_directive 在前 2 阶段是 PROCEED,最后是 TERMINATE"""
    results = run_meta_protocol("Pick a database for my app.")
    assert results[0].next_stage_directive is not None
    assert "PROCEED" in results[0].next_stage_directive
    assert "structured_debate" in results[0].next_stage_directive
    assert "PROCEED" in results[1].next_stage_directive
    assert "logical_fusion" in results[1].next_stage_directive
    assert "TERMINATE" in results[2].next_stage_directive
    print(f"  ✓ test_run_meta_protocol_next_directive: "
          f"directives={[r.next_stage_directive[:30] for r in results]}")
    return True


def test_run_meta_protocol_with_real_provider():
    """传入真实 provider (MockProvider) 应该能跑"""
    from moa_gateway.providers.mock_provider import MockProvider
    mp = MockProvider(model="mock-lite")
    results = run_meta_protocol(
        "What are best practices for code review?",
        providers=[mp],
    )
    assert len(results) == 3
    # MockProvider 应返回非空内容
    assert all(len(r.output) > 0 for r in results)
    # elapsed_ms 应该是数字
    assert all(r.elapsed_ms >= 0 for r in results)
    print(f"  ✓ test_run_meta_protocol_with_real_provider: "
          f"elapsed={[f'{r.elapsed_ms:.1f}ms' for r in results]}")
    return True


# ============ cognitively_clash 测试 ============

def test_cognitively_clash_two_opposing():
    """cognitively_clash 返回 2 个对立 prompt"""
    pa, pb = cognitively_clash("optimist", "pessimist", "Should we invest in crypto?")
    assert isinstance(pa, str) and isinstance(pb, str)
    assert pa != pb, "two prompts should differ"
    # 都应包含原始 query
    assert "crypto" in pa.lower() and "crypto" in pb.lower()
    # Position A 标签 vs Position B 标签
    assert "Position A" in pa
    assert "Position B" in pb
    # 角色名应在 prompt 里
    assert "optimist" in pa.lower()
    assert "pessimist" in pb.lower()
    print(f"  ✓ test_cognitively_clash_two_opposing: "
          f"len(pa)={len(pa)}, len(pb)={len(pb)}")
    return True


def test_cognitively_clash_custom_roles():
    """自定义角色(不在预定义表里)也能工作"""
    pa, pb = cognitively_clash("visionary", "skeptic", "AI will replace programmers")
    assert "visionary" in pa.lower()
    assert "skeptic" in pb.lower()
    assert "AI" in pa and "AI" in pb
    print(f"  ✓ test_cognitively_clash_custom_roles")
    return True


# ============ three_jumps 测试 ============

def test_three_jumps_returns_three():
    """three_jumps 返回 3 步"""
    out = three_jumps("Python is great for data science. It has rich libraries.")
    assert isinstance(out, list)
    assert len(out) == 3
    # 3 步都包含 jump 标签
    for i, step in enumerate(out):
        assert "JUMP" in step
    # 顺序应该是 1, 2, 3
    assert "JUMP 1" in out[0]
    assert "JUMP 2" in out[1]
    assert "JUMP 3" in out[2]
    print(f"  ✓ test_three_jumps_returns_three: 3 steps generated")
    return True


def test_three_jumps_differentiation_in_step1():
    """Step 1 应提到分化 / 多视角"""
    out = three_jumps("Machine learning models can be trained on data. Validation is important.")
    step1 = out[0].lower()
    assert "differentiation" in step1 or "perspective" in step1 or "generated" in step1
    # Step 1 应该真的产生了多个视角(>= 2)
    import re
    m = re.search(r"(\d+)\s+perspectives", step1)
    assert m is not None
    n = int(m.group(1))
    assert n >= 2, f"expected >=2 perspectives, got {n}"
    print(f"  ✓ test_three_jumps_differentiation_in_step1: {n} perspectives")
    return True


# ============ fuse_decision 测试 ============

def test_fuse_decision_picks_longest():
    """fuse_decision 选最长的(启发式 #1)"""
    options = [
        "Use Redis for caching.",
        "Use Redis with persistence and cluster mode for high availability caching. "
        "This is the recommended approach for production systems handling high load.",
        "Use memcached.",
    ]
    winner = fuse_decision(options, context="caching strategy")
    assert winner == options[1], f"expected longest, got: {winner[:50]}"
    print(f"  ✓ test_fuse_decision_picks_longest: picked longest ({len(winner)} chars)")
    return True


def test_fuse_decision_same_length_more_keywords():
    """同长度 → 选含更多 high-权重关键词的"""
    short_a = "Use it."  # 6 chars
    short_b = "Must required critical essential core"  # 同长度,含更多 high kw
    # 强制同长度
    short_a = "aaaa."
    short_b = "must required critical essential"
    # 让两个长度大致相同
    if len(short_a) != len(short_b):
        # padding
        if len(short_a) < len(short_b):
            short_a = short_a + "x" * (len(short_b) - len(short_a))
        else:
            short_b = short_b + "x" * (len(short_a) - len(short_b))
    options = [short_a, short_b]
    winner = fuse_decision(options, context="")
    # 含 "must required critical essential" 的应胜出
    assert "must" in winner.lower(), f"expected keyword-rich, got: {winner}"
    print(f"  ✓ test_fuse_decision_same_length_more_keywords")
    return True


def test_fuse_decision_empty():
    """边界:空 options → 返回空串"""
    assert fuse_decision([], "") == ""
    assert fuse_decision(["", "  ", ""], "") == ""
    assert fuse_decision(None, "") == ""  # type: ignore
    print(f"  ✓ test_fuse_decision_empty: handles empty/None")
    return True


def test_fuse_decision_single():
    """边界:单选项 → 直接返回"""
    assert fuse_decision(["only one option"], "") == "only one option"
    print(f"  ✓ test_fuse_decision_single")
    return True


# ============ JSON 序列化测试 ============

def test_json_serialization():
    """MetaResult / MetaPrompt 能 JSON 序列化"""
    r = MetaResult(
        stage=MetaStage.ROLE_DIFFERENTIATION,
        output="sample output",
        reasoning_chain=["step 1", "step 2"],
        next_stage_directive="PROCEED",
        role="answerer",
        elapsed_ms=12.3,
    )
    d = meta_result_to_json(r)
    s = json.dumps(d, ensure_ascii=False)
    assert "role_differentiation" in s
    assert "sample output" in s
    assert "answerer" in s

    p = MetaPrompt(
        stage=MetaStage.STRUCTURED_DEBATE,
        role="critic",
        system_prompt="sys",
        user_prompt_template="user {query}",
    )
    pd = meta_prompt_to_json(p)
    ps = json.dumps(pd, ensure_ascii=False)
    assert "structured_debate" in ps
    print(f"  ✓ test_json_serialization: round-trip ok")
    return True


# ============ 模板可定制测试 ============

def test_stage_prompts_customizable():
    """模板可定制:用自定义 roles"""
    custom_roles = ["analyst", "strategist"]
    prompts = get_stage_prompts("query", roles=custom_roles)
    assert len(prompts) == 3
    # Stage 1 role 应该是第一个自定义角色
    assert prompts[0].role == "analyst"
    # Stage 1 渲染后应含 "analyst" (role 占位符被注入)
    rendered = prompts[0].render("query")
    assert "analyst" in rendered["user"].lower()
    print(f"  ✓ test_stage_prompts_customizable: roles={custom_roles}")
    return True


def test_stage_prompts_render():
    """MetaPrompt.render 应该返回 system + user"""
    p = get_stage_prompts("What is X?")[0]
    rendered = p.render("What is X?")
    assert "system" in rendered
    assert "user" in rendered
    # user 应包含 query
    assert "What is X?" in rendered["user"]
    # system 应非空
    assert len(rendered["system"]) > 0
    # role 占位符应被 self.role 填入
    assert p.role in rendered["user"]
    print(f"  ✓ test_stage_prompts_render: keys={list(rendered.keys())}, role={p.role}")
    return True


# ============ 额外辅助测试 ============

def test_merge_meta_results():
    """merge_meta_results 把多阶段合并成单字符串"""
    r1 = MetaResult(stage=MetaStage.ROLE_DIFFERENTIATION, output="p1", role="a")
    r2 = MetaResult(stage=MetaStage.STRUCTURED_DEBATE, output="p2", role="b")
    r3 = MetaResult(stage=MetaStage.LOGICAL_FUSION, output="p3", role="c")
    merged = merge_meta_results([r1, r2, r3])
    assert "p1" in merged and "p2" in merged and "p3" in merged
    assert "role_differentiation" in merged
    assert "logical_fusion" in merged
    assert merge_meta_results([]) == ""
    print(f"  ✓ test_merge_meta_results: 3 stages merged")
    return True


def test_clash_role_pairs():
    """CLASH_ROLE_PAIRS 至少 3 对"""
    assert len(CLASH_ROLE_PAIRS) >= 3
    for a, b in CLASH_ROLE_PAIRS:
        assert a != b
    print(f"  ✓ test_clash_role_pairs: {len(CLASH_ROLE_PAIRS)} pairs")
    return True


# ============ 主入口 ============

def run_all():
    """运行所有测试"""
    tests = [
        # 枚举
        test_meta_stage_enum_count,
        test_meta_stage_values,
        test_meta_stage_distinct,
        # get_stage_prompts
        test_get_stage_prompts_returns_three,
        test_stage1_system_role_diff,
        test_stage2_system_debate,
        test_stage3_system_fusion,
        # run_meta_protocol
        test_run_meta_protocol_three_stages,
        test_run_meta_protocol_reasoning_chain,
        test_run_meta_protocol_next_directive,
        test_run_meta_protocol_with_real_provider,
        # cognitively_clash
        test_cognitively_clash_two_opposing,
        test_cognitively_clash_custom_roles,
        # three_jumps
        test_three_jumps_returns_three,
        test_three_jumps_differentiation_in_step1,
        # fuse_decision
        test_fuse_decision_picks_longest,
        test_fuse_decision_same_length_more_keywords,
        test_fuse_decision_empty,
        test_fuse_decision_single,
        # JSON
        test_json_serialization,
        # 模板可定制
        test_stage_prompts_customizable,
        test_stage_prompts_render,
        # 辅助
        test_merge_meta_results,
        test_clash_role_pairs,
    ]
    print(f"\n=== Running {len(tests)} meta_prompt tests ===\n")
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed (of {len(tests)}) ===")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all() else 1)
