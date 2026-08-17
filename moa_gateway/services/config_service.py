"""ConfigService — wraps config_stack, mx_annot, checkpoint, artifact, acceptance, action_policy, tool_replay, brainstorm (override).

Exposes:
  - config(action, key, value, layer, explicit, mode, layers)
  - mx(action, text, file_path, language, command)
  - checkpoint(action, name, payload)
  - artifact(action, id, name, type, description, tags, inputs, outputs, dependencies, pane_id, command, cwd, env_vars)
  - acceptance(action, criterion, root_id, criteria, text)
  - action_policy(command, rules)
  - tool_replay(proposals, window)
  - brainstorm_decide(topic, options)  # different from quality.brainstorm
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import asdict

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


def _load_config_stack():
    from ..capability.config_stack import (
        ConfigEntry,
        ConfigLayer,
        ConfigStack,
        PermissionMode,
        PermissionRegistry,
        merge_layers,
        stack_to_dict,
    )

    return (
        ConfigStack,
        ConfigLayer,
        ConfigEntry,
        stack_to_dict,
        merge_layers,
        PermissionRegistry,
        PermissionMode,
    )


def _load_mx_annot():
    # Audit fix: real API is parse_mx_annotations / compute_fanin / mx_cli
    # (parse / fanin / cli do not exist).
    from ..capability.mx_annot import (
        compute_fanin,
        mx_cli,
        parse_mx_annotations,
    )

    return parse_mx_annotations, compute_fanin, mx_cli


# Audit fix: checkpoint exposes the CheckpointStore class (no module-level
# save/load/list_all/delete). One process-wide store rooted in a temp dir.
_checkpoint_store = None


def _get_checkpoint_store():
    global _checkpoint_store
    from ..capability.checkpoint import CheckpointStore

    if _checkpoint_store is None:
        root = os.path.join(tempfile.gettempdir(), "moa_gateway_service_checkpoints")
        _checkpoint_store = CheckpointStore(root_dir=root, max_keep=10)
    return _checkpoint_store


def _load_checkpoint():
    from ..capability.checkpoint import CheckpointStore

    return CheckpointStore


# Audit fix: artifact exposes Artifact / ArtifactType / SchemaRegistry /
# TmuxOrchestrator / TmuxPane (no module-level register/list_by_type/...).
_artifact_registry = None
_tmux_orchestrator = None


def _get_artifact_registry():
    global _artifact_registry
    from ..capability.artifact import SchemaRegistry

    if _artifact_registry is None:
        _artifact_registry = SchemaRegistry()
    return _artifact_registry


def _get_tmux_orchestrator():
    global _tmux_orchestrator
    from ..capability.artifact import TmuxOrchestrator

    if _tmux_orchestrator is None:
        _tmux_orchestrator = TmuxOrchestrator(max_visible=3)
    return _tmux_orchestrator


def _load_artifact():
    from ..capability.artifact import (
        Artifact,
        ArtifactType,
        SchemaRegistry,
        TmuxOrchestrator,
        TmuxPane,
    )

    return Artifact, ArtifactType, SchemaRegistry, TmuxOrchestrator, TmuxPane


# Audit fix: acceptance exposes parse_ears / validate_pattern +
# AcceptanceCriterion / AcceptanceTree (no module-level add/get_tree).
_acceptance_trees: dict[str, object] = {}


def _load_acceptance():
    from ..capability.acceptance import (
        AcceptanceCriterion,
        AcceptanceTree,
        parse_ears,
        tree_to_dict,
        validate_pattern,
    )

    return validate_pattern, parse_ears, AcceptanceCriterion, AcceptanceTree, tree_to_dict


def _load_action_policy():
    # Audit fix: real API is ActionPolicy.evaluate / default_safe_policy /
    # detect_bypass (no module-level `evaluate`).
    from ..capability.action_policy import (
        ActionPolicy,
        PolicyRule,
        default_safe_policy,
        detect_bypass,
    )

    return ActionPolicy, PolicyRule, default_safe_policy, detect_bypass


def _load_tool_replay():
    # Audit fix: real entry point is replay_tool_calls (no `replay`).
    from ..capability.tool_replay import (
        detect_tool_loop,
        extract_tool_calls,
        replay_tool_calls,
    )

    return replay_tool_calls, detect_tool_loop, extract_tool_calls


def _load_brainstorm():
    # Audit fix: brainstorm has no module-level `decide`; DecideMode is the
    # real decision generator.
    from ..capability.brainstorm import DecideMode

    return DecideMode


def _criterion_from_dict(d: dict, AcceptanceCriterion):
    valid = {k: v for k, v in d.items() if k in AcceptanceCriterion.__dataclass_fields__}
    return AcceptanceCriterion(**valid)


def _criterion_to_dict(c) -> dict:
    d = asdict(c)
    if d.get("pattern") is not None and hasattr(d["pattern"], "value"):
        d["pattern"] = d["pattern"].value
    return d


def _artifact_to_dict(a) -> dict:
    d = asdict(a)
    if hasattr(d.get("type"), "value"):
        d["type"] = d["type"].value
    return d


def _resolve_layer(ConfigLayer, layer):
    """Accept int values (0..7) or member names (POLICY/USER/...)."""
    if layer is None:
        return ConfigLayer.USER
    if isinstance(layer, int):
        return ConfigLayer(layer)
    name = str(layer).upper()
    try:
        return ConfigLayer[name]
    except KeyError as e:
        valid = [m.name for m in ConfigLayer]
        raise ValueError(f"unknown layer: {layer!r}, expected int 0..7 or one of {valid}") from e


class ConfigService(ServiceBase):
    name = "config"
    description = "配置 / 状态 / artifact / 模板"

    def _register_methods(self):
        self._methods["config"] = ServiceMethod(
            name="config",
            description="配置 stack (get/set/unset/merge/permission/snapshot); layer: 0..7 或 POLICY/USER/PROJECT/LOCAL/PLUGIN/SKILL/SESSION/BUILTIN",
            func=self.config,
            input_required=["action"],
        )
        self._methods["mx"] = ServiceMethod(
            name="mx",
            description="mx annotation (parse/fanin/cli) — parse_mx_annotations + compute_fanin + mx_cli(list/count/find)",
            func=self.mx,
            input_required=["action"],
            input_optional=["text", "file_path", "language", "command"],
        )
        self._methods["checkpoint"] = ServiceMethod(
            name="checkpoint",
            description="checkpoint 持久化 (CheckpointStore: save/load/list/delete)",
            func=self.checkpoint,
            input_required=["action"],
            input_optional=["name", "payload"],
        )
        self._methods["artifact"] = ServiceMethod(
            name="artifact",
            description="artifact 管理 (SchemaRegistry register/list_by_type/validate + TmuxOrchestrator add_pane/layout/safe_layout)",
            func=self.artifact,
            input_required=["action"],
        )
        self._methods["acceptance"] = ServiceMethod(
            name="acceptance",
            description="acceptance criteria (validate_pattern/add/parse_ears/get_tree)",
            func=self.acceptance,
            input_required=["action"],
        )
        self._methods["action_policy"] = ServiceMethod(
            name="action_policy",
            description="action 策略评估 (ActionPolicy.evaluate + bypass 检测)",
            func=self.action_policy,
            input_required=["command"],
            input_optional=["rules"],
        )
        self._methods["tool_replay"] = ServiceMethod(
            name="tool_replay",
            description="tool call replay: replay_tool_calls 聚合去重 + detect_tool_loop 循环检测",
            func=self.tool_replay,
            input_required=["proposals"],
            input_optional=["window"],
        )
        self._methods["brainstorm_decide"] = ServiceMethod(
            name="brainstorm_decide",
            description="brainstorm 决策 (DecideMode.generate_advocates)",
            func=self.brainstorm_decide,
            input_required=["topic", "options"],
        )

    def config(
        self, action, key=None, value=None, layer=None, explicit=False, mode=None, layers=None
    ):
        # Audit fix: ConfigStack.get returns the value (not an entry); layers
        # are IntEnum 0..7; PermissionRegistry has set_rule/check (no `mode`).
        (
            ConfigStack,
            ConfigLayer,
            _ConfigEntry,
            stack_to_dict,
            merge_layers,
            PermissionRegistry,
            PermissionMode,
        ) = _load_config_stack()
        if not hasattr(self, "_stack"):
            self._stack = ConfigStack()
        if not hasattr(self, "_perm_registry"):
            self._perm_registry = PermissionRegistry()
        if action == "get":
            value_found, source = self._stack.get_with_source(key or "")
            return {
                "value": value_found,
                "layer": source.value if source is not None else None,
                "layer_name": source.name if source is not None else None,
            }
        if action == "set":
            self._stack.set(key or "", value, layer=_resolve_layer(ConfigLayer, layer), explicit=bool(explicit))
            return {"ok": True, "key": key, "value": value}
        if action == "unset":
            removed = self._stack.unset(key or "", layer=_resolve_layer(ConfigLayer, layer))
            return {"ok": True, "key": key, "removed": removed}
        if action == "merge":
            typed = {}
            for k, v in (layers or {}).items():
                typed[_resolve_layer(ConfigLayer, k)] = v or {}
            return {"merged": merge_layers(typed)}
        if action == "permission":
            reg = self._perm_registry
            if mode and key:
                # set a rule for a tool pattern
                reg.set_rule(str(key), PermissionMode(str(mode)), reason="service dispatch")
                return {
                    "ok": True,
                    "tool_pattern": str(key),
                    "mode": str(mode),
                    "rules": [asdict(r) for r in reg.all_rules()],
                }
            if key:
                resolved = reg.check(str(key))
                return {
                    "tool_pattern": str(key),
                    "mode": resolved.value,
                    "rules": [asdict(r) for r in reg.all_rules()],
                }
            return {
                "default_mode": mode or "default",
                "rules": [asdict(r) for r in reg.all_rules()],
            }
        if action == "snapshot":
            return {"stack": stack_to_dict(self._stack)}
        raise ValueError(f"unknown action: {action}")

    def mx(self, action, text=None, file_path=None, language="python", command=None):
        # Audit fix: real API — parse_mx_annotations / compute_fanin / mx_cli.
        parse, fanin, cli = _load_mx_annot()
        if action == "parse":
            annotations = parse(text or "", file_path or "", language)
            return {
                "annotations": [
                    {**asdict(a), "tag": a.tag.value if hasattr(a.tag, "value") else str(a.tag)}
                    for a in annotations
                ],
                "count": len(annotations),
            }
        if action == "fanin":
            annotations = parse(text or "", file_path or "", language)
            return {"fanin": fanin(annotations)}
        if action == "cli":
            annotations = parse(text or "", file_path or "", language)
            return {"output": cli(annotations, command or "list")}
        raise ValueError(f"unknown action: {action}")

    def checkpoint(self, action, name=None, payload=None):
        # Audit fix: drive the real CheckpointStore.
        _CheckpointStore = _load_checkpoint()
        store = _get_checkpoint_store()
        if action == "save":
            path = store.save(name=name or "", payload=payload or {})
            return {"ok": True, "path": path}
        if action == "load":
            return {"payload": store.load(name=name or "")}
        if action == "list":
            return {"checkpoints": store.list()}
        if action == "delete":
            return {"deleted": store.delete(name=name or "")}
        raise ValueError(f"unknown action: {action}")

    def artifact(
        self,
        action,
        id=None,
        name=None,
        type=None,
        description=None,
        tags=None,
        inputs=None,
        outputs=None,
        dependencies=None,
        pane_id=None,
        command=None,
        cwd=None,
        env_vars=None,
    ):
        # Audit fix: drive SchemaRegistry + TmuxOrchestrator classes.
        Artifact, ArtifactType, _SchemaRegistry, _TmuxOrchestrator, TmuxPane = _load_artifact()
        registry = _get_artifact_registry()
        orch = _get_tmux_orchestrator()

        def _build_artifact():
            try:
                atype = ArtifactType(str(type or "agent"))
            except ValueError as e:
                valid = [t.value for t in ArtifactType]
                raise ValueError(f"unknown artifact type: {type!r}, expected one of {valid}") from e
            return Artifact(
                id=str(id or ""),
                name=str(name or ""),
                type=atype,
                description=str(description or ""),
                tags=list(tags or []),
                inputs=dict(inputs or {}),
                outputs=dict(outputs or {}),
                dependencies=list(dependencies or []),
            )

        if action == "register":
            art = _build_artifact()
            registry.register(art)
            return {"registered": True, "artifact": _artifact_to_dict(art), "errors": registry.validate(art)}
        if action == "list_by_type":
            try:
                atype = ArtifactType(str(type or "agent"))
            except ValueError as e:
                valid = [t.value for t in ArtifactType]
                raise ValueError(f"unknown artifact type: {type!r}, expected one of {valid}") from e
            return {"artifacts": [_artifact_to_dict(a) for a in registry.list_by_type(atype)]}
        if action == "validate":
            art = _build_artifact()
            errors = registry.validate(art)
            return {"errors": errors, "valid": len(errors) == 0}
        if action == "add_pane":
            pane = TmuxPane(
                pane_id=str(pane_id or ""),
                command=str(command or ""),
                cwd=str(cwd or "."),
                env_vars=dict(env_vars or {}),
            )
            orch.add_pane(pane)
            return {"added": True, "pane_id": pane.pane_id, "panes_total": len(orch.layout())}
        if action == "layout":
            return {"layout": [asdict(p) for p in orch.layout()]}
        if action == "safe_layout":
            return {"layout": [asdict(p) for p in orch.safe_layout()], "overflow": [asdict(p) for p in orch.overflow()]}
        raise ValueError(f"unknown action: {action}")

    def acceptance(self, action, criterion=None, root_id=None, criteria=None, text=None):
        # Audit fix: real API — parse_ears / validate_pattern +
        # AcceptanceCriterion / AcceptanceTree.
        validate_pattern, parse_ears, AcceptanceCriterion, AcceptanceTree, tree_to_dict = (
            _load_acceptance()
        )
        if action == "validate_pattern":
            if not isinstance(criterion, dict):
                raise ValueError("criterion must be a dict {id, given, when, then}")
            ac = _criterion_from_dict(criterion, AcceptanceCriterion)
            return {"pattern": validate_pattern(ac), "criterion": _criterion_to_dict(ac)}
        if action == "add":
            rid = str(root_id or "")
            tree = _acceptance_trees.get(rid)
            if tree is None:
                tree = AcceptanceTree(root_id=rid)
                _acceptance_trees[rid] = tree
            added = []
            for c in criteria or []:
                if not isinstance(c, dict):
                    raise ValueError("each criterion must be a dict")
                ac = _criterion_from_dict(c, AcceptanceCriterion)
                tree.add_criterion(ac)
                added.append(ac.id)
            return {"root_id": rid, "added": added, "tree": tree_to_dict(tree)}
        if action == "parse_ears":
            parsed = parse_ears(text=text or "")
            return {"parsed": [_criterion_to_dict(c) for c in parsed]}
        if action == "get_tree":
            rid = str(root_id or "")
            tree = _acceptance_trees.get(rid)
            if tree is None:
                return {"tree": None, "root_id": rid}
            return {"tree": tree_to_dict(tree), "root_id": rid}
        raise ValueError(f"unknown action: {action}")

    def action_policy(self, command, rules=None):
        # Audit fix: build an ActionPolicy (default safe policy when no rules)
        # and call its evaluate(); also report bypass detection.
        ActionPolicy, PolicyRule, default_safe_policy, detect_bypass = _load_action_policy()
        if rules:
            policy = ActionPolicy()
            for r in rules:
                if not isinstance(r, dict):
                    raise ValueError("each rule must be a dict {name, action, pattern, ...}")
                valid = {k: v for k, v in r.items() if k in PolicyRule.__dataclass_fields__}
                policy.add_rule(PolicyRule(**valid))
        else:
            policy = default_safe_policy()
        verdict = policy.evaluate(str(command))
        out = asdict(verdict)
        out["bypass_detections"] = [asdict(b) for b in detect_bypass(str(command))]
        return out

    def tool_replay(self, proposals, window=5):
        # Audit fix: real entry point is replay_tool_calls + detect_tool_loop.
        # Loop detection runs over the RAW extracted sequence (pre-dedup) so
        # repeated identical calls are actually observable.
        replay_tool_calls, detect_tool_loop, extract_tool_calls = _load_tool_replay()
        if not isinstance(proposals, list):
            raise ValueError("proposals must be a list of proposal texts")
        texts = [str(p) for p in proposals]
        raw_calls = []
        for idx, text in enumerate(texts):
            raw_calls.extend(extract_tool_calls(text, proposal_idx=idx))
        result = replay_tool_calls(texts)
        loop = detect_tool_loop(raw_calls, window=int(window))
        return {
            "tool_calls": [asdict(tc) for tc in result.tool_calls],
            "aggregated_arguments": result.aggregated_arguments,
            "deduplicated_count": result.deduplicated_count,
            "conflicts_resolved": result.conflicts_resolved,
            "loop_detected": asdict(loop) if loop is not None else None,
        }

    def brainstorm_decide(self, topic, options):
        # Audit fix: DecideMode is the real decision generator.
        DecideMode = _load_brainstorm()
        dm = DecideMode(str(topic), [str(o) for o in (options or [])])
        return {"topic": str(topic), "advocates": dm.generate_advocates()}
