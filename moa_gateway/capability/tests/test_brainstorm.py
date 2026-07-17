"""brainstorm 真实测试(非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.brainstorm import (
    HEURISTIC_GENERATORS,
    PERSONA_TEMPLATES,
    BrainstormSession,
    DecideMode,
    PersonaType,
    build_persona,
    sanitize_advocate_id,
)

# ============ 1. PersonaType 枚举完整性(5 个) ============

def test_persona_type_count():
    """应有 5 个发散人格"""
    assert len(PersonaType) == 5, f"expected 5, got {len(PersonaType)}"
    print(f"  ✓ test_persona_type_count ({len(PersonaType)} personas)")
    return True


def test_persona_type_members():
    """5 个名字都对得上"""
    expected = {
        "radical_innovator",
        "cross_industry_transplanter",
        "first_principles_thinker",
        "devils_advocate",
        "user_empathy_champion",
    }
    actual = {p.value for p in PersonaType}
    assert actual == expected, f"mismatch: {actual ^ expected}"
    print(f"  ✓ test_persona_type_members ({sorted(actual)})")
    return True


def test_persona_type_unique():
    """5 个值唯一"""
    vals = [p.value for p in PersonaType]
    assert len(set(vals)) == len(vals), "duplicate values"
    print("  ✓ test_persona_type_unique")
    return True


def test_persona_type_is_str():
    """PersonaType 是 str enum(可 JSON 序列化)"""
    p = PersonaType.RADICAL_INNOVATOR
    assert isinstance(p, str)
    assert p == "radical_innovator"
    print("  ✓ test_persona_type_is_str")
    return True


def test_persona_type_iteration_order():
    """枚举可迭代且顺序稳定"""
    vals = [p.value for p in PersonaType]
    assert vals == [
        "radical_innovator",
        "cross_industry_transplanter",
        "first_principles_thinker",
        "devils_advocate",
        "user_empathy_champion",
    ]
    print("  ✓ test_persona_type_iteration_order")
    return True


# ============ 2. BrainstormSession 默认 5 persona ============

def test_brainstorm_default_personas():
    """默认全开 5 persona"""
    s = BrainstormSession("How to reduce LLM hallucination?")
    assert len(s.personas) == 5, f"expected 5, got {len(s.personas)}"
    assert set(s.personas) == set(PersonaType)
    print(f"  ✓ test_brainstorm_default_personas ({len(s.personas)})")
    return True


def test_brainstorm_selected_personas():
    """选定 persona 列表"""
    chosen = [PersonaType.RADICAL_INNOVATOR, PersonaType.DEVILS_ADVOCATE]
    s = BrainstormSession("AI safety", personas=chosen)
    assert s.personas == chosen
    assert len(s.personas) == 2
    print(f"  ✓ test_brainstorm_selected_personas ({len(s.personas)})")
    return True


# ============ 3. generate_ideas ============

def test_generate_ideas_default_5():
    """默认 5 persona → 5 ideas"""
    s = BrainstormSession("Design a fair review system")
    ideas = s.generate_ideas()
    assert len(ideas) == 5
    assert set(ideas.keys()) == set(PersonaType)
    for p, text in ideas.items():
        assert isinstance(text, str) and len(text) > 0, f"empty idea for {p}"
    print(f"  ✓ test_generate_ideas_default_5 (avg_len={sum(len(v) for v in ideas.values()) // 5})")
    return True


def test_generate_ideas_selected_count():
    """选定 3 persona → 3 ideas"""
    chosen = [
        PersonaType.RADICAL_INNOVATOR,
        PersonaType.DEVILS_ADVOCATE,
        PersonaType.USER_EMPATHY_CHAMPION,
    ]
    s = BrainstormSession("Topic", personas=chosen)
    ideas = s.generate_ideas()
    assert len(ideas) == 3
    assert set(ideas.keys()) == set(chosen)
    print(f"  ✓ test_generate_ideas_selected_count ({len(ideas)})")
    return True


# ============ 4. 5 persona 模板含 topic ============

def test_radical_innovator_template_contains_topic():
    """RADICAL_INNOVATOR 模板含 topic"""
    s = BrainstormSession("smart contracts")
    p = build_persona(PersonaType.RADICAL_INNOVATOR, s.topic)
    rendered = p.render_template(s.topic)
    assert "smart contracts" in rendered
    assert "re-imagined" in rendered.lower()
    print("  ✓ test_radical_innovator_template_contains_topic")
    return True


def test_cross_industry_transplanter_template_contains_topic():
    """CROSS_INDUSTRY_TRANSPLANTER 模板含 topic"""
    s = BrainstormSession("appointment booking")
    p = build_persona(PersonaType.CROSS_INDUSTRY_TRANSPLANTER, s.topic)
    rendered = p.render_template(s.topic)
    assert "appointment booking" in rendered
    assert any(ind in rendered.lower() for ind in ["aviation", "healthcare", "finance"])
    print("  ✓ test_cross_industry_transplanter_template_contains_topic")
    return True


def test_first_principles_thinker_template_contains_topic():
    """FIRST_PRINCIPLES_THINKER 模板含 topic"""
    s = BrainstormSession("voting systems")
    p = build_persona(PersonaType.FIRST_PRINCIPLES_THINKER, s.topic)
    rendered = p.render_template(s.topic)
    assert "voting systems" in rendered
    assert "assumption" in rendered.lower()
    print("  ✓ test_first_principles_thinker_template_contains_topic")
    return True


def test_devils_advocate_template_contains_topic():
    """DEVILS_ADVOCATE 模板含 topic"""
    s = BrainstormSession("autonomous vehicles")
    p = build_persona(PersonaType.DEVILS_ADVOCATE, s.topic)
    rendered = p.render_template(s.topic)
    assert "autonomous vehicles" in rendered
    assert "wrong" in rendered.lower()
    print("  ✓ test_devils_advocate_template_contains_topic")
    return True


def test_user_empathy_champion_template_contains_topic():
    """USER_EMPATHY_CHAMPION 模板含 topic"""
    s = BrainstormSession("mobile banking")
    p = build_persona(PersonaType.USER_EMPATHY_CHAMPION, s.topic)
    rendered = p.render_template(s.topic)
    assert "mobile banking" in rendered
    assert "user" in rendered.lower() or "feel" in rendered.lower()
    print("  ✓ test_user_empathy_champion_template_contains_topic")
    return True


def test_all_5_templates_distinct():
    """5 个 template 互相不同"""
    templates = [PERSONA_TEMPLATES[p]["template"] for p in PersonaType]
    assert len(set(templates)) == 5, "templates not all distinct"
    print("  ✓ test_all_5_templates_distinct")
    return True


# ============ 5. 启发式生成 ============

def test_radical_innovator_heuristic():
    """RADICAL_INNOVATOR 启发式产出含 topic 与 'Radical' 标记"""
    s = BrainstormSession("online education")
    ideas = s.generate_ideas()
    text = ideas[PersonaType.RADICAL_INNOVATOR]
    assert "online education" in text
    assert "Radical" in text or "10x" in text or "rebuild" in text.lower()
    print(f"  ✓ test_radical_innovator_heuristic (len={len(text)})")
    return True


def test_devils_advocate_heuristic():
    """DEVILS_ADVOCATE 启发式产出含 4 类风险"""
    s = BrainstormSession("vaccine rollout")
    ideas = s.generate_ideas()
    text = ideas[PersonaType.DEVILS_ADVOCATE]
    assert "vaccine rollout" in text
    # 至少提到 3 个风险关键词
    risk_kws = ["performance", "safety", "ux", "ethic", "risk", "fail"]
    hits = sum(1 for k in risk_kws if k.lower() in text.lower())
    assert hits >= 3, f"too few risk keywords, only {hits}"
    print(f"  ✓ test_devils_advocate_heuristic (risk_hits={hits})")
    return True


def test_persona_system_prompt_contains_topic():
    """所有 persona system_prompt 含 topic"""
    topic = "carbon capture"
    s = BrainstormSession(topic)
    personas = s.personas_for_topic()
    assert len(personas) == 5
    for p in personas:
        assert topic in p.system_prompt, f"{p.persona_type} missing topic"
    print("  ✓ test_persona_system_prompt_contains_topic (5/5)")
    return True


# ============ 6. 边界 ============

def test_brainstorm_empty_personas():
    """边界:0 persona"""
    s = BrainstormSession("nothing", personas=[])
    assert s.personas == []
    ideas = s.generate_ideas()
    assert ideas == {}
    print("  ✓ test_brainstorm_empty_personas")
    return True


# ============ 7. DecideMode ============

def test_decide_mode_init():
    """DecideMode 初始化"""
    d = DecideMode("Which DB?", ["Postgres", "Mongo"])
    assert d.topic == "Which DB?"
    assert d.options == ["Postgres", "Mongo"]
    print("  ✓ test_decide_mode_init")
    return True


def test_decide_mode_3_options_3_advocates():
    """3 options → 3 advocate"""
    opts = ["PostgreSQL", "MongoDB", "SQLite"]
    d = DecideMode("Database choice", opts)
    advs = d.generate_advocates()
    assert len(advs) == 3
    expected_ids = {f"advocate_{sanitize_advocate_id(o)}" for o in opts}
    assert set(advs.keys()) == expected_ids
    for _aid, text in advs.items():
        assert isinstance(text, str) and len(text) > 0
    print(f"  ✓ test_decide_mode_3_options_3_advocates ({sorted(advs.keys())})")
    return True


def test_advocate_template_contains_option():
    """advocate_<option> 模板含 option 文本"""
    opts = ["Use Rust", "Stay with Python"]
    d = DecideMode("Language choice", opts)
    advs = d.generate_advocates()
    for o in opts:
        aid = f"advocate_{sanitize_advocate_id(o)}"
        assert aid in advs
        assert o in advs[aid], f"{aid} missing option '{o}'"
    print("  ✓ test_advocate_template_contains_option")
    return True


def test_advocate_id_sanitization():
    """option 文本 → advocate id 清洗"""
    assert sanitize_advocate_id("Hello World") == "hello_world"
    assert sanitize_advocate_id("PostgreSQL 16!") == "postgresql_16"
    assert sanitize_advocate_id("中文选项") == "中文选项"
    assert sanitize_advocate_id("   ") == "option"
    assert sanitize_advocate_id("@#$%") == "option"
    print("  ✓ test_advocate_id_sanitization")
    return True


def test_decide_mode_empty_options():
    """边界:0 options"""
    d = DecideMode("nothing", [])
    advs = d.generate_advocates()
    assert advs == {}
    detailed = d.generate_advocates_detailed()
    assert detailed == {}
    print("  ✓ test_decide_mode_empty_options")
    return True


# ============ 8. JSON 序列化 ============

def test_brainstorm_json_serialization():
    """BrainstormSession.to_json() 是合法 JSON,含 topic + 5 ideas"""
    s = BrainstormSession("test topic", personas=list(PersonaType))
    js = s.to_json()
    obj = json.loads(js)
    assert obj["topic"] == "test topic"
    assert len(obj["ideas"]) == 5
    assert set(obj["ideas"].keys()) == {p.value for p in PersonaType}
    print("  ✓ test_brainstorm_json_serialization (5 ideas serialized)")
    return True


def test_decide_mode_json_serialization():
    """DecideMode.to_json() 是合法 JSON"""
    d = DecideMode("DB", ["A", "B"])
    js = d.to_json()
    obj = json.loads(js)
    assert obj["topic"] == "DB"
    assert obj["options"] == ["A", "B"]
    assert "advocate_a" in obj["advocates"]
    assert "advocate_b" in obj["advocates"]
    print("  ✓ test_decide_mode_json_serialization")
    return True


def test_brainstorm_idea_to_dict():
    """BrainstormIdea.to_dict() 把 enum 转成 value"""
    s = BrainstormSession("X")
    detailed = s.generate_ideas_detailed()
    first = next(iter(detailed.values()))
    d = first.to_dict()
    assert d["persona_type"] == first.persona_type.value
    assert isinstance(d["persona_type"], str)
    assert d["template"]
    assert d["idea"]
    print("  ✓ test_brainstorm_idea_to_dict")
    return True


# ============ 9. 多次 generate 一致性 ============

def test_multiple_generates_deterministic():
    """同一 session 多次 generate_ideas 产出完全一致(启发式无随机性)"""
    s = BrainstormSession("stable topic")
    a = s.generate_ideas()
    b = s.generate_ideas()
    c = s.generate_ideas()
    assert a == b == c, "non-deterministic heuristic"
    print("  ✓ test_multiple_generates_deterministic (3 runs identical)")
    return True


def test_different_topics_different_ideas():
    """不同 topic → 启发式产出明显不同"""
    s1 = BrainstormSession("quantum networking")
    s2 = BrainstormSession("kindergarten playground")
    i1 = s1.generate_ideas()[PersonaType.RADICAL_INNOVATOR]
    i2 = s2.generate_ideas()[PersonaType.RADICAL_INNOVATOR]
    assert i1 != i2
    assert "quantum networking" in i1
    assert "kindergarten playground" in i2
    print("  ✓ test_different_topics_different_ideas")
    return True


# ============ 10. DecideMode adv 内容 ============

def test_advocate_argues_for_option():
    """advocate 文本必须为 option 站台(出现 'FOR' 或 'arguing')"""
    d = DecideMode("Topic", ["Option A"])
    advs = d.generate_advocates()
    text = advs["advocate_option_a"]
    assert "Option A" in text
    assert "FOR" in text or "arguing" in text.lower() or "favor" in text.lower()
    print("  ✓ test_advocate_argues_for_option")
    return True


def test_5_personas_all_have_heuristics():
    """5 persona 都注册了启发式"""
    for p in PersonaType:
        assert p in HEURISTIC_GENERATORS, f"missing heuristic for {p}"
        fn = HEURISTIC_GENERATORS[p]
        out = fn("test")
        assert isinstance(out, str) and len(out) > 0
    print("  ✓ test_5_personas_all_have_heuristics (5/5)")
    return True


if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for f in funcs:
        try:
            if f():
                passed += 1
        except Exception as e:
            print(f"  ✗ {f.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} tests passed")
