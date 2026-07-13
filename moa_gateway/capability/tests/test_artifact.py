"""Tests for A-21 Artifact Schema + A-50 Tmux Orchestrator
风格对齐 secret_scan: assert ... 全 pass,无 mock。
"""
from __future__ import annotations

import json
import time

import pytest

from moa_gateway.capability.artifact import (
    Artifact,
    ArtifactType,
    SchemaRegistry,
    TmuxPane,
    TmuxOrchestrator,
)


# ============ A-21: ArtifactType 枚举 (5 个) ============

class TestArtifactType:
    def test_artifact_type_has_5_values(self) -> None:
        assert len(ArtifactType) == 5

    def test_artifact_type_agent(self) -> None:
        assert ArtifactType.AGENT.value == "agent"

    def test_artifact_type_skill(self) -> None:
        assert ArtifactType.SKILL.value == "skill"

    def test_artifact_type_connector(self) -> None:
        assert ArtifactType.CONNECTOR.value == "connector"

    def test_artifact_type_action(self) -> None:
        assert ArtifactType.ACTION.value == "action"

    def test_artifact_type_experiment_plan(self) -> None:
        assert ArtifactType.EXPERIMENT_PLAN.value == "experiment_plan"

    def test_artifact_type_is_str(self) -> None:
        # str-mix 枚举,值可比较
        assert ArtifactType.AGENT == "agent"


# ============ A-21: Artifact 字段 ============

class TestArtifact:
    def test_artifact_defaults(self) -> None:
        a = Artifact(
            id="a1",
            name="demo",
            type=ArtifactType.AGENT,
            description="x",
        )
        assert a.id == "a1"
        assert a.name == "demo"
        assert a.type == ArtifactType.AGENT
        assert a.version == "1.0.0"
        assert a.schema_version == 1
        assert a.description == "x"
        assert a.tags == []
        assert a.inputs == {}
        assert a.outputs == {}
        assert a.dependencies == []
        # created_at 自动填充,接近 now
        assert isinstance(a.created_at, float)
        assert a.created_at <= time.time()

    def test_artifact_with_full_fields(self) -> None:
        a = Artifact(
            id="x",
            name="X",
            type=ArtifactType.SKILL,
            description="d",
            version="2.3.4",
            schema_version=2,
            tags=["t1", "t2"],
            inputs={"q": "str"},
            outputs={"r": "int"},
            dependencies=["dep1"],
            created_at=123.0,
        )
        assert a.version == "2.3.4"
        assert a.schema_version == 2
        assert a.tags == ["t1", "t2"]
        assert a.inputs == {"q": "str"}
        assert a.outputs == {"r": "int"}
        assert a.dependencies == ["dep1"]
        assert a.created_at == 123.0

    def test_artifact_to_dict(self) -> None:
        a = Artifact(id="a", name="a", type=ArtifactType.ACTION, description="d")
        d = a.to_dict()
        assert d["id"] == "a"
        assert d["type"] == "action"  # 序列化为 value
        assert d["version"] == "1.0.0"


# ============ A-21: SchemaRegistry ============

def _make_artifact(aid: str, t: ArtifactType, **kw) -> Artifact:
    return Artifact(id=aid, name=aid, type=t, description=f"desc-{aid}", **kw)


class TestSchemaRegistry:
    def test_register_and_get(self) -> None:
        reg = SchemaRegistry()
        a = _make_artifact("a1", ArtifactType.AGENT)
        reg.register(a)
        got = reg.get("a1")
        assert got is a

    def test_get_missing_returns_none(self) -> None:
        reg = SchemaRegistry()
        assert reg.get("nope") is None

    def test_list_by_type_filters(self) -> None:
        reg = SchemaRegistry()
        reg.register(_make_artifact("a1", ArtifactType.AGENT))
        reg.register(_make_artifact("a2", ArtifactType.AGENT))
        reg.register(_make_artifact("s1", ArtifactType.SKILL))
        reg.register(_make_artifact("c1", ArtifactType.CONNECTOR))
        reg.register(_make_artifact("act1", ArtifactType.ACTION))
        reg.register(_make_artifact("ep1", ArtifactType.EXPERIMENT_PLAN))

        agents = reg.list_by_type(ArtifactType.AGENT)
        assert len(agents) == 2
        assert {a.id for a in agents} == {"a1", "a2"}

        skills = reg.list_by_type(ArtifactType.SKILL)
        assert len(skills) == 1
        assert skills[0].id == "s1"

    def test_list_by_type_empty(self) -> None:
        reg = SchemaRegistry()
        assert reg.list_by_type(ArtifactType.SKILL) == []

    def test_list_by_type_invalid(self) -> None:
        reg = SchemaRegistry()
        with pytest.raises(TypeError):
            reg.list_by_type("agent")  # type: ignore[arg-type]

    def test_register_invalid(self) -> None:
        reg = SchemaRegistry()
        with pytest.raises(TypeError):
            reg.register({"id": "x"})  # type: ignore[arg-type]

    def test_register_overwrites(self) -> None:
        reg = SchemaRegistry()
        reg.register(_make_artifact("x", ArtifactType.AGENT))
        reg.register(_make_artifact("x", ArtifactType.SKILL))
        assert reg.get("x").type == ArtifactType.SKILL
        assert len(reg) == 1


# ============ A-21: validate ============

class TestValidate:
    def test_validate_complete(self) -> None:
        reg = SchemaRegistry()
        a = _make_artifact("a", ArtifactType.AGENT)
        assert reg.validate(a) == []

    def test_validate_missing_id(self) -> None:
        reg = SchemaRegistry()
        a = Artifact(id="", name="n", type=ArtifactType.AGENT, description="d")
        # id 是空字符串 → 视为缺失
        missing = reg.validate(a)
        assert "id" in missing

    def test_validate_missing_name(self) -> None:
        reg = SchemaRegistry()
        a = Artifact(id="a", name="", type=ArtifactType.AGENT, description="d")
        missing = reg.validate(a)
        assert "name" in missing

    def test_validate_missing_description(self) -> None:
        reg = SchemaRegistry()
        a = Artifact(id="a", name="n", type=ArtifactType.AGENT, description="")
        missing = reg.validate(a)
        assert "description" in missing


# ============ A-21: 5 type 独立 list ============

class TestFiveTypesIndependent:
    def test_five_types_each_have_own_list(self) -> None:
        reg = SchemaRegistry()
        # 每种类型放 2 个,共 10 个
        for i in range(2):
            reg.register(_make_artifact(f"a{i}", ArtifactType.AGENT))
            reg.register(_make_artifact(f"s{i}", ArtifactType.SKILL))
            reg.register(_make_artifact(f"c{i}", ArtifactType.CONNECTOR))
            reg.register(_make_artifact(f"act{i}", ArtifactType.ACTION))
            reg.register(_make_artifact(f"ep{i}", ArtifactType.EXPERIMENT_PLAN))

        for t in ArtifactType:
            assert len(reg.list_by_type(t)) == 2
        assert len(reg) == 10


# ============ A-50: TmuxPane ============

class TestTmuxPane:
    def test_tmux_pane_defaults(self) -> None:
        p = TmuxPane(pane_id="p1", command="ls", cwd="/tmp")
        assert p.pane_id == "p1"
        assert p.command == "ls"
        assert p.cwd == "/tmp"
        assert p.env_vars == {}

    def test_tmux_pane_with_env(self) -> None:
        p = TmuxPane(
            pane_id="p1",
            command="bash",
            cwd="/home",
            env_vars={"FOO": "bar", "BAZ": "qux"},
        )
        assert p.env_vars == {"FOO": "bar", "BAZ": "qux"}


# ============ A-50: TmuxOrchestrator.add_pane / layout ============

class TestTmuxOrchestrator:
    def test_add_pane(self) -> None:
        orch = TmuxOrchestrator(max_visible=3)
        orch.add_pane(TmuxPane(pane_id="p1", command="ls", cwd="/"))
        orch.add_pane(TmuxPane(pane_id="p2", command="pwd", cwd="/"))
        assert len(orch) == 2

    def test_layout_max_visible_3(self) -> None:
        orch = TmuxOrchestrator(max_visible=3)
        for i in range(3):
            orch.add_pane(TmuxPane(pane_id=f"p{i}", command="echo", cwd="/"))
        visible = orch.layout()
        assert len(visible) == 3
        assert [p.pane_id for p in visible] == ["p0", "p1", "p2"]

    def test_layout_truncates_over_3(self) -> None:
        orch = TmuxOrchestrator(max_visible=3)
        for i in range(7):
            orch.add_pane(TmuxPane(pane_id=f"p{i}", command="echo", cwd="/"))
        visible = orch.layout()
        assert len(visible) == 3
        assert [p.pane_id for p in visible] == ["p0", "p1", "p2"]
        # overflow 含 4 个
        assert len(orch.overflow()) == 4

    def test_layout_zero_panes(self) -> None:
        orch = TmuxOrchestrator(max_visible=3)
        assert orch.layout() == []
        assert orch.overflow() == []
        assert len(orch) == 0

    def test_invalid_max_visible(self) -> None:
        with pytest.raises(ValueError):
            TmuxOrchestrator(max_visible=-1)
        with pytest.raises(ValueError):
            TmuxOrchestrator(max_visible="3")  # type: ignore[arg-type]


# ============ A-50: sensitive_env_safe ============

class TestSensitiveEnvSafe:
    def test_safe_pane(self) -> None:
        orch = TmuxOrchestrator()
        p = TmuxPane(
            pane_id="p1",
            command="ls -la /tmp",
            cwd="/tmp",
            env_vars={"FOO": "bar"},
        )
        assert orch.sensitive_env_safe(p) is True

    def test_command_with_secret_unsafe(self) -> None:
        orch = TmuxOrchestrator()
        p = TmuxPane(
            pane_id="p1",
            command="curl -H 'X-Api-Key: abc' https://example.com",
            cwd="/",
            env_vars={},
        )
        assert orch.sensitive_env_safe(p) is False

    def test_command_with_password_unsafe(self) -> None:
        orch = TmuxOrchestrator()
        p = TmuxPane(
            pane_id="p1",
            command="mysql -u root --password=hunter2",
            cwd="/",
            env_vars={},
        )
        assert orch.sensitive_env_safe(p) is False

    def test_command_with_token_unsafe(self) -> None:
        orch = TmuxOrchestrator()
        p = TmuxPane(
            pane_id="p1",
            command="export TOKEN=xyz123",
            cwd="/",
            env_vars={},
        )
        assert orch.sensitive_env_safe(p) is False

    def test_env_key_with_secret_unsafe(self) -> None:
        orch = TmuxOrchestrator()
        # command 安全,但 env key 含 secret
        p = TmuxPane(
            pane_id="p1",
            command="echo hi",
            cwd="/",
            env_vars={"API_KEY": "abc"},
        )
        assert orch.sensitive_env_safe(p) is False

    def test_env_key_with_password_unsafe(self) -> None:
        orch = TmuxOrchestrator()
        p = TmuxPane(
            pane_id="p1",
            command="echo hi",
            cwd="/",
            env_vars={"DB_PASSWORD": "x"},
        )
        assert orch.sensitive_env_safe(p) is False

    def test_safe_layout_filters(self) -> None:
        orch = TmuxOrchestrator(max_visible=3)
        orch.add_pane(TmuxPane(pane_id="p1", command="ls", cwd="/", env_vars={"FOO": "1"}))
        orch.add_pane(TmuxPane(pane_id="p2", command="echo SECRET=1", cwd="/", env_vars={}))
        orch.add_pane(TmuxPane(pane_id="p3", command="pwd", cwd="/", env_vars={}))
        safe = orch.safe_layout()
        ids = [p.pane_id for p in safe]
        assert "p1" in ids
        assert "p2" not in ids
        assert "p3" in ids


# ============ JSON 序列化 ============

class TestJsonSerialization:
    def test_artifact_to_json_roundtrip(self) -> None:
        a = Artifact(
            id="a1",
            name="demo",
            type=ArtifactType.AGENT,
            description="测试",
            tags=["中文", "tag"],
            inputs={"q": "string"},
        )
        s = a.to_json()
        d = json.loads(s)
        assert d["id"] == "a1"
        assert d["name"] == "demo"
        assert d["type"] == "agent"
        assert d["description"] == "测试"
        assert d["tags"] == ["中文", "tag"]
        assert d["inputs"] == {"q": "string"}
        assert d["version"] == "1.0.0"
        assert d["schema_version"] == 1

    def test_registry_to_dict(self) -> None:
        reg = SchemaRegistry()
        reg.register(_make_artifact("a1", ArtifactType.AGENT))
        reg.register(_make_artifact("s1", ArtifactType.SKILL))
        d = reg.to_dict()
        assert d["count"] == 2
        assert "agent" in d["by_type"]
        assert "skill" in d["by_type"]
        assert d["by_type"]["agent"] == ["a1"]
        assert d["by_type"]["skill"] == ["s1"]
        # 可 JSON 化
        s = json.dumps(d, ensure_ascii=False)
        assert "a1" in s

    def test_tmux_orchestrator_to_dict(self) -> None:
        orch = TmuxOrchestrator(max_visible=2)
        orch.add_pane(TmuxPane(pane_id="p1", command="ls", cwd="/tmp", env_vars={"A": "1"}))
        orch.add_pane(TmuxPane(pane_id="p2", command="pwd", cwd="/"))
        orch.add_pane(TmuxPane(pane_id="p3", command="echo", cwd="/"))  # overflow
        d = orch.to_dict()
        assert d["max_visible"] == 2
        assert d["total_panes"] == 3
        assert len(d["visible_panes"]) == 2
        s = json.dumps(d, ensure_ascii=False)
        assert "p1" in s
        assert "p2" in s
        assert "p3" not in s  # overflow 不在 visible_panes

    def test_tmux_pane_to_dict(self) -> None:
        p = TmuxPane(pane_id="p1", command="ls", cwd="/", env_vars={"K": "v"})
        d = p.to_dict()
        assert d == {"pane_id": "p1", "command": "ls", "cwd": "/", "env_vars": {"K": "v"}}
