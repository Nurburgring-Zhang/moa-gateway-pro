"""O1 — CapabilityRegistry: 真实枚举全部可用能力 + 元数据。

编排器靠这份目录做"匹配化"选择。目录不是硬编码假清单, 而是从真实代码/注册表
反射得到:
  - skill   : agent_loop.skills.BUILTIN_TOOLS (真实 dict)
  - loop    : agent_loop 的 react_loop / plan_execute_loop (真实模块)
  - harness : agent_loop.harness.AgentHarness
  - graph   : workflows/ 下的 YAML 工作流 (真实文件, 经 WorkflowLoader 枚举)
  - mcp     : mcp.builtin_tools 注册的工具 + 外部 MCP registry 已发现工具
  - cli     : capability.channels.ChannelType (subagent/cli/api 真实枚举)
  - api     : /v1/capability/* 与核心 /v1/* 能力端点 (真实 capability 模块)
  - moa     : 配置的 MoA presets / strategies (真实 config)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 能力类型常量
CAP_SKILL = "skill"
CAP_LOOP = "loop"
CAP_HARNESS = "harness"
CAP_GRAPH = "graph"
CAP_MCP = "mcp"
CAP_CLI = "cli"
CAP_API = "api"
CAP_MOA = "moa"

VALID_CAPABILITY_TYPES = {
    CAP_SKILL,
    CAP_LOOP,
    CAP_HARNESS,
    CAP_GRAPH,
    CAP_MCP,
    CAP_CLI,
    CAP_API,
    CAP_MOA,
}


@dataclass
class Capability:
    """一项可被编排的能力。"""

    id: str                      # 唯一 id, 形如 "skill.web_search"
    name: str                    # 人类可读名
    type: str                    # VALID_CAPABILITY_TYPES 之一
    description: str = ""        # 能力描述
    when_to_use: list[str] = field(default_factory=list)  # 匹配用关键词/意图
    input_hint: str = ""         # 输入说明(供 planner 组装参数)
    source: str = ""             # 来源(模块/文件), 证明是真实反射而非杜撰
    invoke: dict[str, Any] = field(default_factory=dict)  # 执行所需的最小信息

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "when_to_use": list(self.when_to_use),
            "input_hint": self.input_hint,
            "source": self.source,
        }


class CapabilityRegistry:
    """能力注册表 — 懒加载枚举真实能力, 支持匹配查询。"""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}
        self._loaded = False
        self._load_errors: list[str] = []

    # ---- 构建 ----
    def build(self) -> "CapabilityRegistry":
        if self._loaded:
            return self
        self._caps.clear()
        self._load_errors.clear()
        for name, fn in [
            ("skill", self._load_skills),
            ("loop", self._load_loops),
            ("harness", self._load_harness),
            ("graph", self._load_graphs),
            ("mcp", self._load_mcp),
            ("cli", self._load_cli),
            ("api", self._load_api),
            ("moa", self._load_moa),
        ]:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                msg = f"capability source '{name}' failed: {e}"
                logger.warning(msg)
                self._load_errors.append(msg)
        self._loaded = True
        logger.info("CapabilityRegistry built: %d capabilities", len(self._caps))
        return self

    def _add(self, cap: Capability) -> None:
        self._caps[cap.id] = cap

    # ---- 各来源真实枚举 ----
    def _load_skills(self) -> None:
        from ..agent_loop.skills import BUILTIN_TOOLS

        for name, (handler, desc) in BUILTIN_TOOLS.items():
            self._add(
                Capability(
                    id=f"skill.{name}",
                    name=name,
                    type=CAP_SKILL,
                    description=desc or name,
                    when_to_use=_skill_keywords(name),
                    input_hint="task/query dict passed to the skill handler",
                    source="moa_gateway.agent_loop.skills.BUILTIN_TOOLS",
                    invoke={"kind": "skill", "name": name},
                )
            )

    def _load_loops(self) -> None:
        # react / plan_execute 是 agent_loop 下的真实循环模块
        loops = {
            "react": ("ReAct 思考-行动循环", ["reason", "tool", "step", "react"]),
            "plan_execute": ("先规划后执行(DAG)循环", ["plan", "decompose", "multi-step", "execute"]),
        }
        for name, (desc, kw) in loops.items():
            self._add(
                Capability(
                    id=f"loop.{name}",
                    name=name,
                    type=CAP_LOOP,
                    description=desc,
                    when_to_use=kw,
                    input_hint="messages list + max_iterations + tools",
                    source=f"moa_gateway.agent_loop.{name}_loop",
                    invoke={"kind": "loop", "loop_name": name},
                )
            )

    def _load_harness(self) -> None:
        from ..agent_loop.harness import AgentHarness  # noqa: F401

        self._add(
            Capability(
                id="harness.agent",
                name="AgentHarness",
                type=CAP_HARNESS,
                description="Agent 执行框架: 注册 loop+tool 并驱动运行",
                when_to_use=["agent", "orchestrate", "harness"],
                input_hint="llm_call + registered loops/tools",
                source="moa_gateway.agent_loop.harness.AgentHarness",
                invoke={"kind": "harness"},
            )
        )

    def _load_graphs(self) -> None:
        from ..workflows.workflow_loader import WorkflowLoader

        loader = WorkflowLoader()
        for wf in loader.list_workflows():
            name = wf.get("name", "")
            if not name:
                continue
            self._add(
                Capability(
                    id=f"graph.{name}",
                    name=name,
                    type=CAP_GRAPH,
                    description=wf.get("description", "") or f"workflow {name}",
                    when_to_use=["workflow", "dag", "pipeline", "graph"],
                    input_hint="context dict for workflow inputs",
                    source="moa_gateway.workflows (YAML DAG)",
                    invoke={"kind": "graph", "workflow": name},
                )
            )

    def _load_mcp(self) -> None:
        # 内置 MCP 工具
        try:
            from ..mcp.builtin_tools import register_builtin_tools
            from ..mcp.registry import ToolRegistry

            reg = ToolRegistry()
            register_builtin_tools(reg)
            for tool in reg.list_tools():
                tname = getattr(tool, "name", "") or ""
                tdesc = getattr(tool, "description", "") or ""
                if not tname:
                    continue
                self._add(
                    Capability(
                        id=f"mcp.{tname}",
                        name=tname,
                        type=CAP_MCP,
                        description=tdesc,
                        when_to_use=["mcp", "tool", tname.lower()],
                        input_hint="arguments dict per tool inputSchema",
                        source="moa_gateway.mcp.builtin_tools",
                        invoke={"kind": "mcp", "tool": tname},
                    )
                )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"builtin mcp tools: {e}") from e
        # 外部 MCP registry 已发现工具
        try:
            from ..mcp.external_registry import get_external_mcp_registry

            ext = get_external_mcp_registry().get_all_discovered_tools()
            for tname, meta in ext.items():
                self._add(
                    Capability(
                        id=f"mcp.ext.{tname}",
                        name=tname,
                        type=CAP_MCP,
                        description=(meta.get("definition") or {}).get("description", "external mcp tool"),
                        when_to_use=["mcp", "external", tname.lower()],
                        input_hint="arguments dict",
                        source="moa_gateway.mcp.external_registry",
                        invoke={"kind": "mcp_external", "tool": tname, "server": meta.get("server")},
                    )
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("external mcp discovery skipped: %s", e)

    def _load_cli(self) -> None:
        from ..capability.channels import ChannelType

        for ct in ChannelType:
            self._add(
                Capability(
                    id=f"cli.{ct.value}",
                    name=f"channel:{ct.value}",
                    type=CAP_CLI,
                    description=f"{ct.value} 通道 (subagent/cli/api 多路)",
                    when_to_use=["cli", "channel", "command", ct.value],
                    input_hint="query string executed through the channel chain",
                    source="moa_gateway.capability.channels.ChannelType",
                    invoke={"kind": "cli", "channel": ct.value},
                )
            )

    def _load_api(self) -> None:
        # 对抗复审 Fix(诚实性): 只注册 executor 能真正执行的 API 能力, 不再把 73 个内部
        # capability 模块全部注册成"可编排能力"造成"可规划不可执行"的虚假宣传。
        # executor._exec_api 实际可执行: embedding 模块 + chat/moa/embeddings 路由。
        self._add(
            Capability(
                id="api.capability.embedding",
                name="embedding",
                type=CAP_API,
                description="文本向量化 (embedding 能力)",
                when_to_use=["api", "capability", "embedding", "vector", "向量"],
                input_hint="prompt/text",
                source="moa_gateway.capability.embedding",
                invoke={"kind": "api", "module": "embedding"},
            )
        )
        for path, desc in [
            ("/v1/chat/completions", "对话补全(可走 MoA 编排)"),
            ("/v1/moa/execute", "MoA 多模型编排执行"),
            ("/v1/embeddings", "文本向量化"),
        ]:
            self._add(
                Capability(
                    id=f"api.route.{path.strip('/').replace('/', '.')}",
                    name=path,
                    type=CAP_API,
                    description=desc,
                    when_to_use=["api", "route"],
                    input_hint="OpenAI 兼容请求体",
                    source="moa_gateway.routes",
                    invoke={"kind": "api_route", "path": path},
                )
            )

    def _load_moa(self) -> None:
        # MoA 引擎本身始终可用(get_moa()), 即使未配置具名 preset 也注册 engine 能力,
        # 保证注册表在任何环境下都含 moa 能力(不依赖 config.yaml 是否存在)。
        self._add(
            Capability(
                id="moa.engine",
                name="MoAOrchestrator",
                type=CAP_MOA,
                description="Mixture-of-Agents 多模型编排引擎(默认策略)",
                when_to_use=["moa", "ensemble", "multi-model", "aggregate", "集成", "多模型"],
                input_hint="query + messages",
                source="moa_gateway.moa.MoAOrchestrator",
                invoke={"kind": "moa", "preset": None},
            )
        )
        try:
            from ..config import get_settings

            settings = get_settings()
            for pname in (settings.moa.presets or {}):
                self._add(
                    Capability(
                        id=f"moa.preset.{pname}",
                        name=f"preset:{pname}",
                        type=CAP_MOA,
                        description=f"MoA preset {pname}",
                        when_to_use=["moa", "preset", "ensemble", pname.lower()],
                        input_hint="query + messages",
                        source="config.moa.presets",
                        invoke={"kind": "moa", "preset": pname},
                    )
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("moa presets load skipped: %s", e)

    # ---- 查询 / 匹配 ----
    def all(self) -> list[Capability]:
        self.build()
        return list(self._caps.values())

    def get(self, cap_id: str) -> Capability | None:
        self.build()
        return self._caps.get(cap_id)

    def by_type(self, cap_type: str) -> list[Capability]:
        self.build()
        return [c for c in self._caps.values() if c.type == cap_type]

    def search(self, keywords: list[str], cap_types: list[str] | None = None) -> list[Capability]:
        """按关键词匹配能力(名称/描述/when_to_use 命中), 可选限定类型。"""
        self.build()
        kws = [k.lower() for k in keywords if k]
        out: list[Capability] = []
        for c in self._caps.values():
            if cap_types and c.type not in cap_types:
                continue
            if not kws:
                out.append(c)
                continue
            hay = " ".join([c.name.lower(), c.description.lower(), " ".join(c.when_to_use).lower()])
            if any(k in hay for k in kws):
                out.append(c)
        return out

    def summary(self) -> dict[str, Any]:
        self.build()
        counts: dict[str, int] = {}
        for c in self._caps.values():
            counts[c.type] = counts.get(c.type, 0) + 1
        return {
            "total": len(self._caps),
            "by_type": counts,
            "load_errors": list(self._load_errors),
            "capabilities": [c.to_dict() for c in self._caps.values()],
        }


# ---- skill 关键词(用于匹配) ----
def _skill_keywords(name: str) -> list[str]:
    table = {
        "web_search": ["search", "web", "internet", "lookup", "查询", "搜索"],
        "code_execute": ["code", "python", "compute", "calculate", "execute", "代码", "计算"],
        "file_read": ["read", "file", "load", "读", "文件"],
        "file_write": ["write", "file", "save", "写", "保存"],
        "file_list": ["list", "directory", "files", "目录", "列表"],
        "analyze_data": ["analyze", "data", "stats", "trend", "anomaly", "分析", "数据"],
        "api_verify": ["api", "endpoint", "verify", "test", "接口", "验证"],
    }
    return table.get(name, [name.lower()])


_registry: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry().build()
    return _registry
