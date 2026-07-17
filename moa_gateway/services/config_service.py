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

from typing import Any, Dict, List

from .base import ServiceBase, ServiceMethod, service_method


def _load_config_stack():
    from ..capability.config_stack import get, set, unset, merge, permission
    return get, set, unset, merge, permission


def _load_mx_annot():
    from ..capability.mx_annot import parse, fanin, cli
    return parse, fanin, cli


def _load_checkpoint():
    from ..capability.checkpoint import save, load, list_all, delete
    return save, load, list_all, delete


def _load_artifact():
    from ..capability.artifact import (
        register, list_by_type, validate, add_pane, layout, safe_layout,
    )
    return register, list_by_type, validate, add_pane, layout, safe_layout


def _load_acceptance():
    from ..capability.acceptance import (
        validate_pattern, add, parse_ears, get_tree,
    )
    return validate_pattern, add, parse_ears, get_tree


def _load_action_policy():
    from ..capability.action_policy import evaluate
    return evaluate


def _load_tool_replay():
    from ..capability.tool_replay import replay
    return replay


def _load_brainstorm():
    from ..capability.brainstorm import decide
    return decide


class ConfigService(ServiceBase):
    name = "config"
    description = "配置 / 状态 / artifact / 模板"

    def _register_methods(self):
        self._methods["config"] = ServiceMethod(
            name="config", description="配置 stack (get/set/unset/merge/permission)",
            func=self.config,
            input_required=["action"],
        )
        self._methods["mx"] = ServiceMethod(
            name="mx", description="mx annotation (parse/fanin/cli)",
            func=self.mx,
            input_required=["action"],
        )
        self._methods["checkpoint"] = ServiceMethod(
            name="checkpoint", description="checkpoint (admin only)",
            func=self.checkpoint,
            input_required=["action"],
        )
        self._methods["artifact"] = ServiceMethod(
            name="artifact", description="artifact 管理 (register/list/validate/pane/layout)",
            func=self.artifact,
            input_required=["action"],
        )
        self._methods["acceptance"] = ServiceMethod(
            name="acceptance", description="acceptance criteria (validate_pattern/add/parse_ears/get_tree)",
            func=self.acceptance,
            input_required=["action"],
        )
        self._methods["action_policy"] = ServiceMethod(
            name="action_policy", description="action 策略评估",
            func=self.action_policy,
            input_required=["command"],
            input_optional=["rules"],
        )
        self._methods["tool_replay"] = ServiceMethod(
            name="tool_replay", description="tool call replay",
            func=self.tool_replay,
            input_required=["proposals"],
        )
        self._methods["brainstorm_decide"] = ServiceMethod(
            name="brainstorm_decide", description="brainstorm 决策",
            func=self.brainstorm_decide,
            input_required=["topic", "options"],
        )

    def config(self, action, key=None, value=None, layer=None, explicit=False,
               mode=None, layers=None):
        get, set, unset, merge, permission = _load_config_stack()
        if action == "get":
            return {"value": get(key=key or "")}
        if action == "set":
            return set(key=key or "", value=value, layer=layer or "user", explicit=explicit)
        if action == "unset":
            return unset(key=key or "", layer=layer or "user")
        if action == "merge":
            return merge(layers=layers or {})
        if action == "permission":
            return {"permission": permission(mode=mode or "default")}
        raise ValueError(f"unknown action: {action}")

    def mx(self, action, text=None, file_path=None, language="python", command=None):
        parse, fanin, cli = _load_mx_annot()
        if action == "parse":
            return parse(text=text or "", file_path=file_path or "", language=language)
        if action == "fanin":
            return fanin(text=text or "", file_path=file_path or "", language=language)
        if action == "cli":
            return cli(text=text or "", file_path=file_path or "",
                        language=language, command=command or "list")
        raise ValueError(f"unknown action: {action}")

    def checkpoint(self, action, name=None, payload=None):
        save, load, list_all, delete = _load_checkpoint()
        if action == "save":
            return {"ok": save(name=name or "", payload=payload or {})}
        if action == "load":
            return {"payload": load(name=name or "")}
        if action == "list":
            return {"checkpoints": list_all()}
        if action == "delete":
            return {"deleted": delete(name=name or "")}
        raise ValueError(f"unknown action: {action}")

    def artifact(self, action, id=None, name=None, type=None, description=None, tags=None,
                  inputs=None, outputs=None, dependencies=None, pane_id=None,
                  command=None, cwd=None, env_vars=None):
        register, list_by_type, validate, add_pane, layout, safe_layout = _load_artifact()
        if action == "register":
            return register(id=id or "", name=name or "", type=type or "agent",
                            description=description or "", tags=tags or [],
                            inputs=inputs or {}, outputs=outputs or {},
                            dependencies=dependencies or [])
        if action == "list_by_type":
            return {"artifacts": list_by_type(type=type or "agent")}
        if action == "validate":
            return validate(id=id or "", name=name or "", type=type or "agent",
                            description=description or "")
        if action == "add_pane":
            return add_pane(pane_id=pane_id or "", command=command or "",
                            cwd=cwd or ".", env_vars=env_vars or {})
        if action == "layout":
            return {"layout": layout()}
        if action == "safe_layout":
            return {"layout": safe_layout()}
        raise ValueError(f"unknown action: {action}")

    def acceptance(self, action, criterion=None, root_id=None, criteria=None, text=None):
        validate_pattern, add, parse_ears, get_tree = _load_acceptance()
        if action == "validate_pattern":
            return validate_pattern(criterion=criterion or {})
        if action == "add":
            return add(root_id=root_id or "", criteria=criteria or [])
        if action == "parse_ears":
            return {"parsed": parse_ears(text=text or "")}
        if action == "get_tree":
            return {"tree": get_tree(root_id=root_id or "")}
        raise ValueError(f"unknown action: {action}")

    def action_policy(self, command, rules=None):
        evaluate = _load_action_policy()
        return evaluate(command=command, rules=rules or [])

    def tool_replay(self, proposals, window=5):
        replay = _load_tool_replay()
        return replay(proposals=proposals, window=window)

    def brainstorm_decide(self, topic, options):
        decide = _load_brainstorm()
        return decide(topic, options=options)
