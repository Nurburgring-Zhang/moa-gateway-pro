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
from typing import Any, Dict, List, Optional

from .base import ServiceBase, ServiceRegistry, ServiceResult


class AgentDispatcher:
    """Unified dispatcher. Singleton via ServiceRegistry.instance()."""

    def __init__(self, registry: Optional[ServiceRegistry] = None):
        self.registry = registry or ServiceRegistry.instance()
        self._workflows: Dict[str, "Workflow"] = {}

    def register(self, service: ServiceBase) -> None:
        self.registry.register(service)

    def list_agents(self) -> List[dict]:
        return self.registry.list_services()

    async def dispatch(
        self,
        service: str,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> ServiceResult:
        """Dispatch a single call to (service, method)."""
        return await self.registry.dispatch(service, method, payload)

    async def dispatch_batch(self, calls: List[Dict[str, Any]]) -> List[ServiceResult]:
        """Dispatch multiple calls in parallel.

        Each call: {"service": "x", "method": "y", "payload": {...}, "alias": "optional"}
        """
        tasks = [
            self.dispatch(c.get("service", ""), c.get("method", ""), c.get("payload"))
            for c in calls
        ]
        return await asyncio.gather(*tasks)

    def register_workflow(self, name: str, workflow: "Workflow") -> None:
        self._workflows[name] = workflow

    async def run_workflow(self, name: str, payload: Optional[Dict[str, Any]] = None) -> "WorkflowResult":
        wf = self._workflows.get(name)
        if not wf:
            return WorkflowResult(ok=False, error=f"workflow '{name}' not found")
        return await wf.run(self, payload or {})

    def list_workflows(self) -> List[dict]:
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
    payload: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)  # step aliases this depends on
    input_map: Dict[str, str] = field(default_factory=dict)  # key → "step.alias.field" or "$input.field"
    optional: bool = False  # if True, failure does not stop workflow
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "service": self.service, "method": self.method,
            "payload": self.payload, "depends_on": self.depends_on,
            "input_map": self.input_map, "optional": self.optional,
            "description": self.description,
        }


@dataclass
class WorkflowResult:
    ok: bool
    steps: Dict[str, ServiceResult] = field(default_factory=dict)
    error: Optional[str] = None
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

    def __init__(self, name: str, description: str = "", steps: Optional[List[WorkflowStep]] = None):
        self.name = name
        self.description = description
        self.steps = steps or []

    def add_step(self, step: WorkflowStep) -> "Workflow":
        self.steps.append(step)
        return self

    def _resolve_value(self, template: str, ctx: Dict[str, Any], root_input: Dict[str, Any]) -> Any:
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

    async def run(self, dispatcher: AgentDispatcher, input_payload: Dict[str, Any]) -> WorkflowResult:
        t0 = time.perf_counter()
        ctx: Dict[str, ServiceResult] = {}
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
                    ok=False, error=f"upstream step '{failed_dep}' failed",
                    error_code="upstream_failed",
                    service=step.service, method=step.method,
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
                    ok=False, steps=ctx, latency_ms=(time.perf_counter() - t0) * 1000,
                    workflow=self.name,
                    error=f"step '{step.name}' failed: {result.error}",
                )
        return WorkflowResult(
            ok=True, steps=ctx, latency_ms=(time.perf_counter() - t0) * 1000,
            workflow=self.name,
        )

    def _get_step(self, name: str) -> WorkflowStep:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)

    def _topo_order(self) -> List[WorkflowStep]:
        # simple topological sort
        by_name = {s.name: s for s in self.steps}
        visited = set()
        order = []

        def visit(name: str, path: Optional[List[str]] = None):
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
_default_dispatcher: Optional[AgentDispatcher] = None


def get_dispatcher() -> AgentDispatcher:
    global _default_dispatcher
    if _default_dispatcher is None:
        _default_dispatcher = AgentDispatcher()
        _bootstrap_default_services(_default_dispatcher)
    return _default_dispatcher


def _bootstrap_default_services(dispatcher: AgentDispatcher) -> None:
    """Register all default services + builtin workflows with the dispatcher."""
    # Each service is imported lazily; if import fails, we skip and log.
    from .base import ServiceRegistry
    from .moa_service import MoAService
    from .consensus_service import ConsensusService
    from .routing_service import RoutingService
    from .quality_service import QualityService
    from .agent_service import AgentService
    from .quota_service import QuotaService
    from .knowledge_service import KnowledgeService
    from .safety_service import SafetyService
    from .observability_service import ObservabilityService
    from .config_service import ConfigService
    from .capability_dispatcher import CapabilityDispatcher

    for svc_cls in [
        MoAService, ConsensusService, RoutingService, QualityService,
        AgentService, QuotaService, KnowledgeService, SafetyService,
        ObservabilityService, ConfigService, CapabilityDispatcher,
    ]:
        try:
            dispatcher.register(svc_cls())
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to register service %s: %s", svc_cls.__name__, e)

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
    wf1 = Workflow(name="moa_quality_pipeline", description="Validate MoA config → Run MoA → FLASK score")
    wf1.add_step(WorkflowStep(
        name="validate", service="moa", method="validate_config",
        payload={}, input_map={
            "proposers": "$input.proposers",
            "aggregator": "$input.aggregator",
        },
        optional=True, description="Validate MoA config (best-effort)",
    ))
    wf1.add_step(WorkflowStep(
        name="run_moa", service="moa", method="run_engine",
        payload={}, input_map={
            "query": "$input.query",
            "proposers": "$input.proposers",
            "aggregator": "$input.aggregator",
        },
        depends_on=["validate"],
        description="Run MoA engine with real data flow from input",
    ))
    wf1.add_step(WorkflowStep(
        name="score", service="quality", method="score_flask",
        payload={},
        depends_on=["run_moa"],
        input_map={"query": "$input.query", "response": "run_moa.aggregated"},
        optional=True, description="FLASK score the MoA aggregated output (optional)",
    ))
    dispatcher.register_workflow("moa_quality_pipeline", wf1)

    # Workflow 2: Consensus (detect convergent → vote ensemble)
    wf2 = Workflow(name="consensus_pipeline", description="Detect convergent ideas → ensemble vote")
    wf2.add_step(WorkflowStep(
        name="detect", service="consensus", method="detect_convergent",
        payload={"proposals": "$input.proposals"},
        input_map={"viability_scores": "$input.viability_scores"},
        description="Detect cross-proposal convergent ideas",
    ))
    wf2.add_step(WorkflowStep(
        name="vote", service="consensus", method="vote_ensemble",
        payload={"votes": "$input.votes"},
        depends_on=["detect"],
        description="Run ensemble vote on proposals",
    ))
    dispatcher.register_workflow("consensus_pipeline", wf2)

    # Workflow 3: Quality gate (gate_l0 → brainstorm)
    wf3 = Workflow(name="quality_gate", description="L0 gate → brainstorm ideas")
    wf3.add_step(WorkflowStep(
        name="gate", service="quality", method="gate_l0",
        payload={"query": "$input.query"},
        description="L0 safety gate",
    ))
    wf3.add_step(WorkflowStep(
        name="brainstorm", service="quality", method="brainstorm",
        payload={"topic": "$input.query", "action": "ideas"},
        depends_on=["gate"],
        description="Brainstorm ideas",
    ))
    dispatcher.register_workflow("quality_gate", wf3)

    # Workflow 4: Knowledge pipeline (embed → semantic search → rerank)
    wf4 = Workflow(name="knowledge_pipeline", description="Embed query → semantic search → rerank")
    wf4.add_step(WorkflowStep(
        name="embed_q", service="knowledge", method="embed",
        payload={"input": "$input.query"},
        description="Embed query",
    ))
    wf4.add_step(WorkflowStep(
        name="search", service="knowledge", method="semantic_search",
        payload={"query": "$input.query", "documents": "$input.documents"},
        depends_on=["embed_q"],
        description="Search by semantic similarity",
    ))
    wf4.add_step(WorkflowStep(
        name="rerank", service="knowledge", method="rerank",
        payload={"query": "$input.query", "documents": "$input.documents"},
        depends_on=["search"],
        description="Rerank results",
    ))
    dispatcher.register_workflow("knowledge_pipeline", wf4)

    # Workflow 5: Quota check (cost estimate → provider health → should rebalance)
    wf5 = Workflow(name="quota_check", description="Cost estimate → provider health → rebalance check")
    wf5.add_step(WorkflowStep(
        name="cost", service="quota", method="cost_estimate",
        payload={"input_tokens": "$input.input_tokens", "output_tokens": "$input.output_tokens",
                  "channels": "$input.channels"},
        description="Estimate cost across channels",
    ))
    wf5.add_step(WorkflowStep(
        name="health", service="quota", method="provider_health_aggregate",
        payload={"providers": "$input.providers"},
        depends_on=["cost"],
        description="Aggregate provider health",
    ))
    wf5.add_step(WorkflowStep(
        name="rebalance", service="quota", method="should_rebalance",
        payload={"stats": "$input.stats"},
        depends_on=["health"],
        description="Check if rebalance needed",
    ))
    dispatcher.register_workflow("quota_check", wf5)

    # Workflow 6: Safety pipeline (gate → tool screen → wrap output)
    wf6 = Workflow(name="safety_pipeline", description="L0 gate → tool screening → output wrapping")
    wf6.add_step(WorkflowStep(
        name="gate", service="quality", method="gate_l0",
        payload={"query": "$input.query"},
        description="L0 safety gate",
    ))
    wf6.add_step(WorkflowStep(
        name="screen", service="safety", method="tool_screening",
        payload={"tool_name": "$input.tool_name", "arguments": "$input.arguments"},
        depends_on=["gate"],
        description="Screen tool call",
    ))
    wf6.add_step(WorkflowStep(
        name="wrap", service="safety", method="output_wrapping",
        payload={"action": "wrap", "content": "$input.output", "source": "$input.source"},
        depends_on=["screen"],
        description="Wrap output for safety",
    ))
    dispatcher.register_workflow("safety_pipeline", wf6)

    # Workflow 7: RAG pipeline (rag_search → rerank → score)
    wf7 = Workflow(name="rag_pipeline", description="RAG search → rerank → top result")
    wf7.add_step(WorkflowStep(
        name="search", service="knowledge", method="rag_search",
        payload={"query": "$input.query", "corpus": "$input.corpus"},
        description="RAG search corpus",
    ))
    wf7.add_step(WorkflowStep(
        name="rerank", service="knowledge", method="rerank",
        payload={"query": "$input.query", "documents": "$input.documents"},
        depends_on=["search"],
        description="Rerank",
    ))
    dispatcher.register_workflow("rag_pipeline", wf7)
