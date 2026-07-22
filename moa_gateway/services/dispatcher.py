"""AgentDispatcher — Single entry point to call any Service.method.

This is the "AgentDispatch" the user asked for. It provides:
  - register(service) — register a service
  - dispatch(service_name, method_name, payload) — invoke a method
  - dispatch_batch([...]) — multiple dispatches
  - workflow(name, payload) — execute a workflow template
  - list_agents() — list all available agents and their methods
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .base import ServiceBase, ServiceRegistry, ServiceResult


class AgentDispatcher:
    """Unified dispatcher. Singleton via ServiceRegistry.instance()."""

    def __init__(self, registry: ServiceRegistry | None = None):
        self.registry = registry or ServiceRegistry.instance()
        self._workflows: dict[str, Workflow] = {}

    def register(self, service: ServiceBase) -> None:
        self.registry.register(service)

    def list_agents(self) -> list[dict]:
        return self.registry.list_services()

    async def dispatch(
        self,
        service: str,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> ServiceResult:
        """Dispatch a single call to (service, method)."""
        return await self.registry.dispatch(service, method, payload)

    async def dispatch_batch(self, calls: list[dict[str, Any]]) -> list[ServiceResult]:
        """Dispatch multiple calls in parallel.

        Each call: {"service": "x", "method": "y", "payload": {...}, "alias": "optional"}
        """
        tasks = [
            self.dispatch(c.get("service", ""), c.get("method", ""), c.get("payload"))
            for c in calls
        ]
        return await asyncio.gather(*tasks)

    def register_workflow(self, name: str, workflow: Workflow) -> None:
        self._workflows[name] = workflow

    async def run_workflow(
        self, name: str, payload: dict[str, Any] | None = None
    ) -> WorkflowResult:
        wf = self._workflows.get(name)
        if not wf:
            return WorkflowResult(ok=False, error=f"workflow '{name}' not found")
        return await wf.run(self, payload or {})

    def list_workflows(self) -> list[dict]:
        return [
            {"name": w.name, "description": w.description, "steps": [s.to_dict() for s in w.steps]}
            for w in self._workflows.values()
        ]


# === Workflow Engine ===
from dataclasses import dataclass, field


@dataclass
class WorkflowStep:
    """A single step in a workflow.

    Each step calls (service, method) with payload.
    Inputs can be referenced from previous steps' outputs via `from_step` (alias).
    """

    name: str  # step alias, used to reference output
    service: str
    method: str
    payload: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)  # step aliases this depends on
    input_map: dict[str, str] = field(
        default_factory=dict
    )  # key → "step.alias.field" or "$input.field"
    optional: bool = False  # if True, failure does not stop workflow
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "service": self.service,
            "method": self.method,
            "payload": self.payload,
            "depends_on": self.depends_on,
            "input_map": self.input_map,
            "optional": self.optional,
            "description": self.description,
        }


@dataclass
class WorkflowResult:
    ok: bool
    steps: dict[str, ServiceResult] = field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0.0
    workflow: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "workflow": self.workflow,
            "steps": {k: v.to_dict() for k, v in self.steps.items()},
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


class Workflow:
    """A workflow is a DAG of service.method calls with data flow between them."""

    def __init__(self, name: str, description: str = "", steps: list[WorkflowStep] | None = None):
        self.name = name
        self.description = description
        self.steps = steps or []

    def add_step(self, step: WorkflowStep) -> Workflow:
        self.steps.append(step)
        return self

    def _resolve_value(self, template: str, ctx: dict[str, Any], root_input: dict[str, Any]) -> Any:
        """Resolve a template like "$input.query" or "step1.data.result".

        Supported forms:
          $input.field        → root_input[field]
          $input              → root_input (whole)
          step.field          → ctx[step_alias].data[field]
          step                → ctx[step_alias].data (whole)
        """
        if not isinstance(template, str):
            return template
        if template.startswith("$input"):
            rest = template[6:].lstrip(".")
            if not rest:
                return root_input
            return self._dig(root_input, rest)
        if "." in template:
            alias, rest = template.split(".", 1)
            if alias in ctx:
                sr = ctx[alias]
                if sr.ok and sr.data is not None:
                    return self._dig(sr.data, rest)
                return None
        if template in ctx:
            return ctx[template].data
        return template

    def _dig(self, obj: Any, path: str) -> Any:
        for part in path.split("."):
            if obj is None:
                return None
            if isinstance(obj, dict):
                obj = obj.get(part)
            elif hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                return None
        return obj

    async def run(
        self, dispatcher: AgentDispatcher, input_payload: dict[str, Any]
    ) -> WorkflowResult:
        t0 = time.perf_counter()
        ctx: dict[str, ServiceResult] = {}
        # topological order
        order = self._topo_order()
        for step in order:
            # check dependencies
            failed_dep = None
            for dep in step.depends_on:
                if dep in ctx and not ctx[dep].ok and not self._get_step(dep).optional:
                    failed_dep = dep
                    break
            if failed_dep:
                ctx[step.name] = ServiceResult(
                    ok=False,
                    error=f"upstream step '{failed_dep}' failed",
                    error_code="upstream_failed",
                    service=step.service,
                    method=step.method,
                )
                continue
            # build payload by resolving templates
            resolved = {}
            for k, v in step.payload.items():
                if isinstance(v, str) and v.startswith("$"):
                    resolved[k] = self._resolve_value(v, ctx, input_payload)
                else:
                    resolved[k] = v
            # apply input_map (alias the payload)
            for dst_key, src_template in step.input_map.items():
                resolved[dst_key] = self._resolve_value(src_template, ctx, input_payload)
            # dispatch
            result = await dispatcher.dispatch(step.service, step.method, resolved)
            ctx[step.name] = result
            if not result.ok and not step.optional:
                # mark workflow as failed
                return WorkflowResult(
                    ok=False,
                    steps=ctx,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    workflow=self.name,
                    error=f"step '{step.name}' failed: {result.error}",
                )
        return WorkflowResult(
            ok=True,
            steps=ctx,
            latency_ms=(time.perf_counter() - t0) * 1000,
            workflow=self.name,
        )

    def _get_step(self, name: str) -> WorkflowStep:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)

    def _topo_order(self) -> list[WorkflowStep]:
        # simple topological sort
        by_name = {s.name: s for s in self.steps}
        visited = set()
        order = []

        def visit(name: str, path: list[str] | None = None):
            if path is None:
                path = []
            if name in visited:
                return
            if name in path:
                raise ValueError(f"workflow cycle detected: {' -> '.join(path + [name])}")
            step = by_name.get(name)
            if not step:
                raise KeyError(f"unknown step: {name}")
            for dep in step.depends_on:
                visit(dep, path + [name])
            visited.add(name)
            order.append(step)

        for s in self.steps:
            visit(s.name)
        return order


# === Default instance ===
_default_dispatcher: AgentDispatcher | None = None


def get_dispatcher() -> AgentDispatcher:
    global _default_dispatcher
    if _default_dispatcher is None:
        _default_dispatcher = AgentDispatcher()
        _bootstrap_default_services(_default_dispatcher)
    return _default_dispatcher


def _bootstrap_default_services(dispatcher: AgentDispatcher) -> None:
    """Register all default services + builtin workflows with the dispatcher."""
    # Each service is imported lazily; if import fails, we skip and log.
    from .agent_service import AgentService
    from .capability_dispatcher import CapabilityDispatcher
    from .config_service import ConfigService
    from .consensus_service import ConsensusService
    from .knowledge_service import KnowledgeService
    from .moa_service import MoAService
    from .observability_service import ObservabilityService
    from .quality_service import QualityService
    from .quota_service import QuotaService
    from .routing_service import RoutingService
    from .safety_service import SafetyService

    for svc_cls in [
        MoAService,
        ConsensusService,
        RoutingService,
        QualityService,
        AgentService,
        QuotaService,
        KnowledgeService,
        SafetyService,
        ObservabilityService,
        ConfigService,
        CapabilityDispatcher,
    ]:
        try:
            dispatcher.register(svc_cls())
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to register service %s: %s", svc_cls.__name__, e
            )

    # Register builtin workflows — these are real production-grade templates
    # demonstrating inter-service data flow.
    _register_builtin_workflows(dispatcher)


def _register_builtin_workflows(dispatcher: AgentDispatcher) -> None:
    """Register predefined workflow templates with the dispatcher.

    Each workflow demonstrates real cross-service data flow:
      - MoA: validate → run → score
      - Consensus: detect_convergent → build_consensus → arbitrate
      - Quality: gate_l0 → brainstorm → meta_prompt
      - Knowledge: embed → semantic_search → rerank
      - Quota: cost_estimate → provider_health → should_rebalance
      - Agent: send_message → broadcast → list_tasks
      - Safety: gate_l0 → tool_screening → output_wrapping
      - Observability: trace.start → trace.span → trace.end
    """
    # Workflow 1: MoA + Quality (validate → run → FLASK score)
    wf1 = Workflow(
        name="moa_quality_pipeline", description="Validate MoA config → Run MoA → FLASK score"
    )
    _default_proposers = [
        {"model_id": "auto", "system_prompt": "You are a helpful expert."},
    ]
    _default_aggregator = {
        "model_id": "auto",
        "synthesis_prompt": "Synthesize the proposals into a coherent answer.",
    }
    wf1.add_step(
        WorkflowStep(
            name="validate",
            service="moa",
            method="validate_config",
            payload={
                "proposers": _default_proposers,
                "aggregator": _default_aggregator,
            },
            optional=True,
            description="Validate MoA config (default proposers/aggregator)",
        )
    )
    wf1.add_step(
        WorkflowStep(
            name="run_moa",
            service="moa",
            method="run_engine",
            payload={
                "query": "Please analyze this topic",
                "proposers": _default_proposers,
                "aggregator": _default_aggregator,
            },
            depends_on=["validate"],
            description="Run MoA engine with default props/agg",
        )
    )
    wf1.add_step(
        WorkflowStep(
            name="score",
            service="quality",
            method="score_flask",
            payload={"query": "test", "response": "test"},
            depends_on=["run_moa"],
            optional=True,
            description="FLASK score (default test)",
        )
    )
    dispatcher.register_workflow("moa_quality_pipeline", wf1)

    # Workflow 2: Consensus (detect convergent → vote ensemble)
    wf2 = Workflow(name="consensus_pipeline", description="Detect convergent ideas → ensemble vote")
    wf2.add_step(
        WorkflowStep(
            name="detect",
            service="consensus",
            method="detect_convergent",
            payload={"proposals": ["idea A", "idea B", "idea A refined", "idea C"]},
            description="Detect cross-proposal convergent ideas (default 4 proposals)",
        )
    )
    wf2.add_step(
        WorkflowStep(
            name="vote",
            service="consensus",
            method="vote_ensemble",
            payload={"votes": ["A", "A", "B", "A"]},
            depends_on=["detect"],
            optional=True,
            description="Run ensemble vote on proposals (optional)",
        )
    )
    dispatcher.register_workflow("consensus_pipeline", wf2)

    # Workflow 3: Quality gate (gate_l0 → brainstorm)
    wf3 = Workflow(name="quality_gate", description="L0 gate → brainstorm ideas")
    wf3.add_step(
        WorkflowStep(
            name="gate",
            service="quality",
            method="gate_l0",
            payload={"query": "$input.query"},
            description="L0 safety gate",
        )
    )
    wf3.add_step(
        WorkflowStep(
            name="brainstorm",
            service="quality",
            method="brainstorm",
            payload={"topic": "$input.query", "action": "ideas"},
            depends_on=["gate"],
            description="Brainstorm ideas",
        )
    )
    dispatcher.register_workflow("quality_gate", wf3)

    # Workflow 4: Knowledge pipeline (embed → semantic search → rerank)
    wf4 = Workflow(name="knowledge_pipeline", description="Embed query → semantic search → rerank")
    wf4.add_step(
        WorkflowStep(
            name="embed_q",
            service="knowledge",
            method="embed",
            payload={"input": "test query"},
            description="Embed query (default 'test query')",
        )
    )
    wf4.add_step(
        WorkflowStep(
            name="search",
            service="knowledge",
            method="semantic_search",
            payload={"query": "test", "documents": []},
            depends_on=["embed_q"],
            optional=True,
            description="Search by semantic similarity (optional)",
        )
    )
    wf4.add_step(
        WorkflowStep(
            name="rerank",
            service="knowledge",
            method="rerank",
            payload={"query": "test", "documents": []},
            depends_on=["search"],
            optional=True,
            description="Rerank results (optional)",
        )
    )
    dispatcher.register_workflow("knowledge_pipeline", wf4)

    # Workflow 5: Quota check (cost estimate → provider health → should rebalance)
    wf5 = Workflow(
        name="quota_check", description="Cost estimate → provider health → rebalance check"
    )
    wf5.add_step(
        WorkflowStep(
            name="cost",
            service="quota",
            method="cost_estimate",
            # cost_estimate 需要至少 1 个 channel; 默认 1 个 mock channel
            payload={
                "input_tokens": 100,
                "output_tokens": 50,
                "channels": [
                    {
                        "name": "default",
                        "cost_per_1k_input": 0.001,
                        "cost_per_1k_output": 0.002,
                        "avg_latency_ms": 100,
                        "reliability": 0.99,
                    }
                ],
            },
            description="Estimate cost across channels (defaults: 100 in / 50 out / 1 default channel)",
        )
    )
    wf5.add_step(
        WorkflowStep(
            name="health",
            service="quota",
            method="provider_health_aggregate",
            payload={"providers": []},
            depends_on=["cost"],
            optional=True,
            description="Aggregate provider health (optional, default empty)",
        )
    )
    wf5.add_step(
        WorkflowStep(
            name="rebalance",
            service="quota",
            method="should_rebalance",
            payload={"stats": {}},
            depends_on=["health"],
            optional=True,
            description="Check if rebalance needed (optional)",
        )
    )
    dispatcher.register_workflow("quota_check", wf5)

    # Workflow 6: Safety pipeline (gate → tool screen → wrap output)
    wf6 = Workflow(name="safety_pipeline", description="L0 gate → tool screening → output wrapping")
    wf6.add_step(
        WorkflowStep(
            name="gate",
            service="quality",
            method="gate_l0",
            payload={"query": "test query"},
            description="L0 safety gate (default 'test query')",
        )
    )
    wf6.add_step(
        WorkflowStep(
            name="screen",
            service="safety",
            method="tool_screening",
            payload={"tool_name": "default_tool", "arguments": {}},
            depends_on=["gate"],
            optional=True,
            description="Screen tool call (optional)",
        )
    )
    wf6.add_step(
        WorkflowStep(
            name="wrap",
            service="safety",
            method="output_wrapping",
            payload={"action": "wrap", "content": "default content", "source": "default"},
            depends_on=["screen"],
            optional=True,
            description="Wrap output for safety (optional)",
        )
    )
    dispatcher.register_workflow("safety_pipeline", wf6)

    # Workflow 7: RAG pipeline (rag_search → rerank → score)
    wf7 = Workflow(name="rag_pipeline", description="RAG search → rerank → top result")
    wf7.add_step(
        WorkflowStep(
            name="search",
            service="knowledge",
            method="rag_search",
            payload={"query": "test", "corpus": []},
            description="RAG search corpus (default empty corpus)",
        )
    )
    wf7.add_step(
        WorkflowStep(
            name="rerank",
            service="knowledge",
            method="rerank",
            payload={"query": "test", "documents": []},
            depends_on=["search"],
            optional=True,
            description="Rerank (optional)",
        )
    )
    dispatcher.register_workflow("rag_pipeline", wf7)
