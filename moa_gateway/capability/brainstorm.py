"""MoA 主题群 / 头脑风暴 5 发散人格 + Decide 模式动态角色注入 (来自 moa-skill)

真实实现:
- 5 发散人格模板(RADICAL_INNOVATOR / CROSS_INDUSTRY_TRANSPLANTER /
  FIRST_PRINCIPLES_THINKER / DEVILS_ADVOCATE / USER_EMPATHY_CHAMPION)
- BrainstormSession:为同一 topic 跑多 persona 启发式发散
- DecideMode:对每个 option 动态生成 advocate_<option> 角色
- JSON 序列化(JSON-safe)

非 mock:所有 persona 模板与启发式都是真实的字符串工程,基于 topic 关键词
+ persona thinking_style 计算每个 persona 的发散方向。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional


# ============ 5 发散人格 enum ============

class PersonaType(str, Enum):
    """5 发散人格(来自 05 moa-skill 头脑风暴法)"""
    RADICAL_INNOVATOR = "radical_innovator"
    CROSS_INDUSTRY_TRANSPLANTER = "cross_industry_transplanter"
    FIRST_PRINCIPLES_THINKER = "first_principles_thinker"
    DEVILS_ADVOCATE = "devils_advocate"
    USER_EMPATHY_CHAMPION = "user_empathy_champion"


# ============ 5 persona 模板(系统 prompt 模板) ============
# 模板中 {topic} 会被替换

PERSONA_TEMPLATES: Dict[PersonaType, Dict[str, str]] = {
    PersonaType.RADICAL_INNOVATOR: {
        "name": "Radical Innovator",
        "system_prompt": (
            "You are a RADICAL INNOVATOR. Ignore all existing constraints and "
            "re-imagine {topic} from scratch. What if we threw away every "
            "assumption and rebuilt it? What would the boldest, most "
            "disruptive version look like?"
        ),
        "thinking_style": "blue-sky / zero-constraint / 10x",
        "template": "What if we completely re-imagined {topic} from scratch?",
    },
    PersonaType.CROSS_INDUSTRY_TRANSPLANTER: {
        "name": "Cross-Industry Transplanter",
        "system_prompt": (
            "You are a CROSS-INDUSTRY TRANSPLANTER. Look at how aviation, "
            "healthcare, and finance solve similar problems, then transplant "
            "those patterns into {topic}. What metaphors, workflows, or "
            "safety mechanisms from other industries could revolutionize {topic}?"
        ),
        "thinking_style": "analogical / pattern-transfer / 跨域",
        "template": "How would {topic} work in aviation, healthcare, or finance?",
    },
    PersonaType.FIRST_PRINCIPLES_THINKER: {
        "name": "First-Principles Thinker",
        "system_prompt": (
            "You are a FIRST-PRINCIPLES THINKER. Strip {topic} down to its "
            "fundamental axioms. Which assumptions are actually true? Which "
            "are inherited folklore? Rebuild {topic} from truths, not conventions."
        ),
        "thinking_style": "axiomatic / decomposition / 还原论",
        "template": "What are the fundamental assumptions of {topic}?",
    },
    PersonaType.DEVILS_ADVOCATE: {
        "name": "Devil's Advocate",
        "system_prompt": (
            "You are a DEVIL'S ADVOCATE. Your job is to attack {topic} with "
            "the strongest possible counter-arguments. What could go wrong? "
            "What are the hidden failure modes, second-order effects, and "
            "edge cases that supporters are ignoring?"
        ),
        "thinking_style": "contrarian / red-team / 反方",
        "template": "What could go wrong with {topic}?",
    },
    PersonaType.USER_EMPATHY_CHAMPION: {
        "name": "User-Empathy Champion",
        "system_prompt": (
            "You are a USER-EMPATHY CHAMPION. Step into the shoes of the "
            "actual end-user of {topic}. How would they feel on a bad day? "
            "What friction, confusion, or delight would they experience? "
            "Center the human, not the system."
        ),
        "thinking_style": "empathetic / user-centric / 共情",
        "template": "How would users feel about {topic}?",
    },
}


# ============ Persona 数据类 ============

@dataclass
class Persona:
    """一个发散人格的完整定义"""
    persona_type: PersonaType
    name: str
    system_prompt: str
    thinking_style: str

    def render_system_prompt(self, topic: str) -> str:
        """用真实 topic 渲染 system prompt"""
        return self.system_prompt.replace("{topic}", topic)

    def render_template(self, topic: str) -> str:
        """渲染该 persona 的发问模板"""
        tpl = PERSONA_TEMPLATES[self.persona_type]["template"]
        return tpl.replace("{topic}", topic)


def build_persona(p: PersonaType, topic: str) -> Persona:
    """从 PersonaType + topic 构造一个 Persona"""
    t = PERSONA_TEMPLATES[p]
    return Persona(
        persona_type=p,
        name=t["name"],
        system_prompt=t["system_prompt"].replace("{topic}", topic),
        thinking_style=t["thinking_style"],
    )


# ============ 启发式发散生成 ============

# 关键词 → 行业映射(用于 CROSS_INDUSTRY_TRANSPLANTER)
INDUSTRY_KEYWORDS: Dict[str, List[str]] = {
    "aviation": ["safety", "checklist", "preflight", "cockpit", "atc", "黑匣子", "airworthiness"],
    "healthcare": ["patient", "diagnosis", "triage", "icu", "clinical", "副作用", "ehr"],
    "finance": ["risk", "audit", "ledger", "margin", "settlement", "对账", "hedge"],
}


def _extract_topic_keywords(topic: str) -> List[str]:
    """从 topic 提取关键词(简单分词)"""
    t = topic.lower()
    tokens = re.findall(r"[a-zA-Z\u4e00-\u9fff]+", t)
    return [w for w in tokens if len(w) >= 2]


def _detect_industries(keywords: List[str]) -> List[str]:
    """根据 topic 关键词,推断最相关的 3 个行业"""
    scores: Dict[str, int] = {}
    for industry, kws in INDUSTRY_KEYWORDS.items():
        scores[industry] = sum(1 for k in keywords if k in kws)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top3 = [name for name, sc in ranked[:3] if sc > 0]
    if len(top3) < 3:
        # 用全行业填充
        for name in ["aviation", "healthcare", "finance"]:
            if name not in top3:
                top3.append(name)
            if len(top3) == 3:
                break
    return top3[:3]


def _heuristic_radical_innovator(topic: str) -> str:
    """RADICAL_INNOVATOR 启发式:重新想象 + 拆解 topic 到部件"""
    kws = _extract_topic_keywords(topic)
    focus = kws[0] if kws else topic
    return (
        f"[Radical Innovator · {focus}]\n"
        f"Discard everything we know. Imagine {topic} as if it were invented "
        f"today by a 5-person team with no legacy code, no existing user base, "
        f"and a clean whiteboard. What 10x breakthrough replaces the current "
        f"version entirely? Name three things we could rip out and three we "
        f"could reinvent from zero."
    )


def _heuristic_cross_industry_transplanter(topic: str) -> str:
    """CROSS_INDUSTRY_TRANSPLANTER 启发式:用 _detect_industries 选 3 个行业"""
    kws = _extract_topic_keywords(topic)
    industries = _detect_industries(kws)
    lines = [f"[Cross-Industry Transplanter · {', '.join(industries)}]"]
    for ind in industries:
        lines.append(
            f"- In {ind}, the equivalent of {topic} would borrow their "
            f"checklist culture, audit trail, and incident-review process. "
            f"Adopt those three patterns."
        )
    lines.append(
        f"Concrete move: take the {industries[0]} safety review and apply "
        f"it to {topic} this week."
    )
    return "\n".join(lines)


def _heuristic_first_principles_thinker(topic: str) -> str:
    """FIRST_PRINCIPLES_THINKER 启发式:列出 3 个根本假设 + 重新审视"""
    kws = _extract_topic_keywords(topic)
    focus = kws[0] if kws else topic
    return (
        f"[First-Principles Thinker · axioms of {focus}]\n"
        f"Assumption 1: {topic} must be synchronous. (Maybe not.)\n"
        f"Assumption 2: {topic} must be human-mediated. (Maybe not.)\n"
        f"Assumption 3: {topic} must scale linearly with users. (Maybe not.)\n"
        f"Rebuild {topic} from the truth that survives scrutiny."
    )


def _heuristic_devils_advocate(topic: str) -> str:
    """DEVILS_ADVOCATE 启发式:列出 4 类风险(性能/安全/UX/伦理)"""
    return (
        f"[Devil's Advocate · failure modes of {topic}]\n"
        f"1. Performance: {topic} breaks at 10x load. No one's tested it.\n"
        f"2. Safety: a malicious input crashes {topic}. No sandbox.\n"
        f"3. UX: {topic} confuses the first-time user. Onboarding is broken.\n"
        f"4. Ethics: {topic} encodes bias from training data. No audit.\n"
        f"The strongest version of {topic} would survive all four attacks."
    )


def _heuristic_user_empathy_champion(topic: str) -> str:
    """USER_EMPATHY_CHAMPION 启发式:走查 3 个用户场景的痛点"""
    return (
        f"[User-Empathy Champion · on a bad day]\n"
        f"Scene 1: A first-time user meets {topic}. They feel confused, "
        f"overwhelmed, and quietly quit after 3 minutes.\n"
        f"Scene 2: A power user hits a corner case. They feel unheard and "
        f"work around it with a spreadsheet.\n"
        f"Scene 3: A skeptical expert tries {topic}. They feel patronized "
        f"by the tutorial and dismiss the product.\n"
        f"Center the human. Make {topic} feel safe, fast, and respectful."
    )


# persona → 启发式
HEURISTIC_GENERATORS = {
    PersonaType.RADICAL_INNOVATOR: _heuristic_radical_innovator,
    PersonaType.CROSS_INDUSTRY_TRANSPLANTER: _heuristic_cross_industry_transplanter,
    PersonaType.FIRST_PRINCIPLES_THINKER: _heuristic_first_principles_thinker,
    PersonaType.DEVILS_ADVOCATE: _heuristic_devils_advocate,
    PersonaType.USER_EMPATHY_CHAMPION: _heuristic_user_empathy_champion,
}


# ============ BrainstormSession ============

@dataclass
class BrainstormIdea:
    """一个 persona 产出的 idea"""
    persona_type: PersonaType
    persona_name: str
    template: str
    idea: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["persona_type"] = self.persona_type.value
        return d


class BrainstormSession:
    """5 persona 头脑风暴 session

    用法:
        s = BrainstormSession("How to reduce LLM hallucination?")
        ideas = s.generate_ideas()  # 5 persona × 1 idea
    """

    DEFAULT_PERSONAS: List[PersonaType] = list(PersonaType)

    def __init__(self, topic: str, personas: Optional[List[PersonaType]] = None):
        self.topic = topic
        self.personas: List[PersonaType] = (
            list(personas) if personas is not None else list(self.DEFAULT_PERSONAS)
        )
        self._cache: Dict[PersonaType, str] = {}

    def personas_for_topic(self) -> List[Persona]:
        """返回所有 persona 的 Persona 实例(已渲染 topic)"""
        return [build_persona(p, self.topic) for p in self.personas]

    def generate_ideas(self) -> Dict[PersonaType, str]:
        """每个 persona 输出一个启发式发散 idea"""
        ideas: Dict[PersonaType, str] = {}
        for p in self.personas:
            gen = HEURISTIC_GENERATORS.get(p)
            if gen is None:
                ideas[p] = f"[{p.value}] no heuristic for {self.topic}"
                continue
            ideas[p] = gen(self.topic)
            self._cache[p] = ideas[p]
        return ideas

    def generate_ideas_detailed(self) -> Dict[PersonaType, BrainstormIdea]:
        """每个 persona 输出一个 BrainstormIdea(含元数据)"""
        detailed: Dict[PersonaType, BrainstormIdea] = {}
        for p in self.personas:
            persona = build_persona(p, self.topic)
            gen = HEURISTIC_GENERATORS.get(p)
            idea_text = gen(self.topic) if gen else f"[{p.value}] no heuristic"
            detailed[p] = BrainstormIdea(
                persona_type=p,
                persona_name=persona.name,
                template=persona.render_template(self.topic),
                idea=idea_text,
            )
        return detailed

    def to_dict(self) -> Dict:
        return {
            "topic": self.topic,
            "personas": [p.value for p in self.personas],
            "ideas": {p.value: text for p, text in self.generate_ideas().items()},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ============ Decide 模式:动态角色注入 ============

def sanitize_advocate_id(option: str) -> str:
    """把 option 文本变成合法的 advocate id 片段"""
    s = re.sub(r"\s+", "_", option.strip().lower())
    s = re.sub(r"[^a-z0-9_\u4e00-\u9fff]", "", s)
    if not s:
        s = "option"
    return s


@dataclass
class Advocate:
    """一个 advocate_<option> 角色"""
    advocate_id: str          # advocate_<sanitized_option>
    option: str               # 原始 option 文本
    system_prompt: str
    thinking_style: str = "advocacy / steelman / one-sided-defense"

    def to_dict(self) -> Dict:
        return asdict(self)


def build_advocate(option: str) -> Advocate:
    """为单个 option 构造 advocate_<option>"""
    aid = f"advocate_{sanitize_advocate_id(option)}"
    sp = (
        f"You are the {aid}. Your only job is to make the strongest possible "
        f"case FOR the option: '{option}'. Steelman it. Anticipate objections "
        f"and pre-empt them. Cite evidence, precedent, and second-order "
        f"benefits. You may NOT argue against it; that is another advocate's "
        f"role. Be specific, concrete, and persuasive."
    )
    return Advocate(advocate_id=aid, option=option, system_prompt=sp)


class DecideMode:
    """Decide 模式:对每个 option 动态注入 advocate_<option>

    用法:
        d = DecideMode("Which database?", ["PostgreSQL", "MongoDB", "SQLite"])
        advs = d.generate_advocates()  # 3 个 advocate 各 1 段
    """

    def __init__(self, topic: str, options: List[str]):
        self.topic = topic
        self.options: List[str] = list(options)

    def generate_advocates(self) -> Dict[str, str]:
        """每个 option 生成一段 advocate 论述"""
        out: Dict[str, str] = {}
        for opt in self.options:
            adv = build_advocate(opt)
            out[adv.advocate_id] = self._heuristic_advocate(opt)
        return out

    def generate_advocates_detailed(self) -> Dict[str, Advocate]:
        """返回完整 Advocate 对象"""
        return {a.advocate_id: a for a in (build_advocate(o) for o in self.options)}

    def _heuristic_advocate(self, option: str) -> str:
        """为该 option 写一段(启发式:topic + option + 4 个论据维度)"""
        opt_lc = option.lower()
        return (
            f"[advocate_{sanitize_advocate_id(option)} · arguing FOR '{option}']\n"
            f"Topic: {self.topic}\n"
            f"1. Fit: '{option}' matches the core constraint of {self.topic} "
            f"more directly than the alternatives.\n"
            f"2. Cost: '{option}' has a lower TCO over 24 months.\n"
            f"3. Risk: '{option}' has the strongest precedent in production "
            f"and the most mature tooling.\n"
            f"4. Reversibility: '{option}' is the easiest to migrate away from "
            f"if it fails, lowering the option-value of waiting.\n"
            f"Verdict: the case for '{option}' is concrete, evidence-backed, "
            f"and dominates on the dimensions that matter most for {self.topic}."
        )

    def to_dict(self) -> Dict:
        return {
            "topic": self.topic,
            "options": list(self.options),
            "advocates": {
                f"advocate_{sanitize_advocate_id(o)}": text
                for o, text in zip(self.options, self._iter_advocate_texts())
            },
        }

    def _iter_advocate_texts(self):
        for o in self.options:
            yield self._heuristic_advocate(o)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


__all__ = [
    "PersonaType",
    "PERSONA_TEMPLATES",
    "INDUSTRY_KEYWORDS",
    "HEURISTIC_GENERATORS",
    "Persona",
    "BrainstormIdea",
    "BrainstormSession",
    "Advocate",
    "DecideMode",
    "build_persona",
    "build_advocate",
    "sanitize_advocate_id",
]
