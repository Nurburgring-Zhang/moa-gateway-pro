"""SafetyService — wraps secret_scan, prompt_canary, tool_screening, output_wrapping, gate_l0, frozen_zone, grace_window, anthropic_compat, llm_merge, version, worktree.

Exposes:
  - secret_scan(path, fail_on)  # admin only
  - prompt_canary(action, prompt, response, canary, strategy)
  - tool_screening(tool_name, arguments)
  - output_wrapping(action, content, source, trust, max_length, wrapped)
  - gate_l0(query)  # duplicated in quality for convenience
  - frozen(action, path, zone, freeze, sentinel, reason, added_at)
  - grace(action, name, at, check_id)
  - anthropic_compat(action, anthropic_request, delta, model, tool_id, name, input, content, is_error, error_type, message, chat_response, judge_response, judge_response_swapped, response_a, response_b)
  - llm_merge(action, strategy, responses, providers, fail_at)
  - version(action, ...)
  - worktree(action, repo_path, repo_path1, repo_path2)  # admin only
"""

from __future__ import annotations

from .base import ServiceBase, ServiceMethod


def _load_secret_scan():
    from ..capability.secret_scan import scan_path, should_block

    return scan_path, should_block


def _load_prompt_canary():
    from ..capability.prompt_canary import check, inject

    return inject, check


def _load_tool_screening():
    from ..capability.tool_screening import ToolScreener, screen_input

    return screen_input, ToolScreener


def _load_output_wrapping():
    from ..capability.output_wrapping import (
        needs_wrapping,
        safe_wrap,
        sanitize_for_prompt,
        unwrap_output,
        wrap_output,
    )

    return wrap_output, safe_wrap, sanitize_for_prompt, needs_wrapping, unwrap_output


def _load_frozen():
    from ..capability.frozen_zone import (
        add,
        assert_modifiable,
        can_modify,
        is_evolvable,
        is_frozen,
        list_sentinels,
    )

    return is_frozen, add, is_evolvable, can_modify, assert_modifiable, list_sentinels


def _load_grace():
    from ..capability.grace_window import (
        register,
        should_block,
        status,
        warnings,
    )

    return register, should_block, status, warnings


def _load_anthropic_compat():
    from ..capability.anthropic_compat import (
        format_error,
        format_response,
        format_sse,
        format_tool_result,
        format_tool_use,
        parse_request,
    )

    return (
        parse_request,
        format_sse,
        format_response,
        format_tool_use,
        format_tool_result,
        format_error,
    )


def _load_llm_merge():
    from ..capability.llm_merge import fallback, merge

    return merge, fallback


def _load_versioning():
    from ..capability.versioning import (
        add,
        get,
        latest,
        parse_battle,
        parse_rating,
        swap_positions_battle,
    )

    return add, get, latest, parse_rating, parse_battle, swap_positions_battle


def _load_worktree():
    from ..capability.worktree import WorktreeManager

    return WorktreeManager


class SafetyService(ServiceBase):
    name = "safety"
    description = "安全 / 兼容性 / 工具筛选 / 输出包装"

    def _register_methods(self):
        self._methods["secret_scan"] = ServiceMethod(
            name="secret_scan",
            description="扫描路径中的 secret (admin only)",
            func=self.secret_scan,
            input_required=["path"],
        )
        self._methods["prompt_canary"] = ServiceMethod(
            name="prompt_canary",
            description="prompt canary (inject / check)",
            func=self.prompt_canary,
            input_required=["action"],
            input_optional=["prompt", "response", "canary", "strategy"],
        )
        self._methods["tool_screening"] = ServiceMethod(
            name="tool_screening",
            description="筛选工具调用(防 rm -rf / 等危险)",
            func=self.tool_screening,
            input_required=["tool_name", "arguments"],
        )
        self._methods["output_wrapping"] = ServiceMethod(
            name="output_wrapping",
            description="输出包装(sanitize/wrap/unwrap/needs_wrapping)",
            func=self.output_wrapping,
            input_required=["action"],
            input_optional=["content", "source", "trust", "max_length", "wrapped"],
        )
        self._methods["frozen"] = ServiceMethod(
            name="frozen",
            description="frozen zone 管理 (is_frozen/add/is_evolvable/can_modify/assert_modifiable/list_sentinels)",
            func=self.frozen,
            input_required=["action"],
        )
        self._methods["grace"] = ServiceMethod(
            name="grace",
            description="grace window (register/should_block/status/warnings)",
            func=self.grace,
            input_required=["action"],
        )
        self._methods["anthropic_compat"] = ServiceMethod(
            name="anthropic_compat",
            description="Anthropic API 兼容(parse/format_sse/format_response/format_tool_*)",
            func=self.anthropic_compat,
            input_required=["action"],
        )
        self._methods["llm_merge"] = ServiceMethod(
            name="llm_merge",
            description="LLM 合并 (merge/fallback)",
            func=self.llm_merge,
            input_required=["action"],
        )
        self._methods["version"] = ServiceMethod(
            name="version",
            description="版本管理(add/get/latest/parse_rating/parse_battle/swap_positions_battle)",
            func=self.version,
            input_required=["action"],
        )
        self._methods["worktree"] = ServiceMethod(
            name="worktree",
            description="worktree 管理 (admin only)",
            func=self.worktree,
            input_required=["action"],
        )

    def secret_scan(self, path, fail_on=99):
        scan_path, _ = _load_secret_scan()
        from pathlib import Path

        # Round-1 (P0-7): path 白名单
        allowed = [
            Path.cwd(),
            Path.cwd() / "moa_gateway",
            Path.cwd() / "scripts",
            Path.cwd() / "src",
            Path.home() / ".moa-gateway",
        ]
        target = Path(path).resolve()
        if not any(str(target).startswith(str(a.resolve())) for a in allowed):
            raise ValueError(f"path not in allowlist: {target}")
        result = scan_path(target)
        return {"path": str(target), "findings": result}

    def prompt_canary(self, action, prompt=None, response=None, canary=None, strategy="suffix"):
        inject, check = _load_prompt_canary()
        if action == "inject":
            return {"prompt": inject(prompt=prompt or "", strategy=strategy), "strategy": strategy}
        if action == "check":
            return check(response=response or "", canary=canary or "")
        raise ValueError(f"unknown action: {action}")

    def tool_screening(self, tool_name, arguments):
        screen_input, ToolScreener = _load_tool_screening()
        screener = ToolScreener()
        findings = screener.screen(tool_name=tool_name, arguments=arguments)
        return {"findings": [f.__dict__ if hasattr(f, "__dict__") else f for f in findings]}

    def output_wrapping(
        self, action, content=None, source=None, trust=None, max_length=None, wrapped=None
    ):
        wrap_output, safe_wrap, sanitize_for_prompt, needs_wrapping, unwrap_output = (
            _load_output_wrapping()
        )
        if action == "wrap":
            return {
                "wrapped": safe_wrap(
                    content=content or "", source=source or "unknown", trust=trust or "untrusted"
                )
            }
        if action == "sanitize":
            return {"sanitized": sanitize_for_prompt(content=content or "")}
        if action == "needs_wrapping":
            return {"needs_wrapping": needs_wrapping(content=content or "")}
        if action == "unwrap":
            return {"content": unwrap_output(wrapped=wrapped or "")}
        raise ValueError(f"unknown action: {action}")

    def frozen(
        self, action, path=None, zone=None, freeze=None, sentinel=None, reason=None, added_at=None
    ):
        is_frozen, add, is_evolvable, can_modify, assert_modifiable, list_sentinels = _load_frozen()
        if action == "is_frozen":
            return {"frozen": is_frozen(path=path or "", zone=zone or "freeze")}
        if action == "add":
            return add(
                path=path or "",
                zone=zone or "freeze",
                sentinel=sentinel or "",
                reason=reason or "",
                added_at=added_at or 0.0,
            )
        if action == "is_evolvable":
            return {"evolvable": is_evolvable(path=path or "")}
        if action == "can_modify":
            return {"can_modify": can_modify(path=path or "", zone=zone or "freeze")}
        if action == "assert_modifiable":
            return {"ok": assert_modifiable(path=path or "")}
        if action == "list_sentinels":
            return {"sentinels": list_sentinels()}
        raise ValueError(f"unknown action: {action}")

    def grace(self, action, name=None, at=None, check_id=None):
        register, should_block, status, warnings = _load_grace()
        if action == "register":
            return register(name=name or "", at=at or 0.0)
        if action == "should_block":
            return {"block": should_block(check_id=check_id or "", at=at or 0.0)}
        if action == "status":
            return status(check_id=check_id or "", at=at or 0.0)
        if action == "warnings":
            return {"warnings": warnings()}
        raise ValueError(f"unknown action: {action}")

    def anthropic_compat(self, action, **kwargs):
        fns = _load_anthropic_compat()
        (
            parse_request,
            format_sse,
            format_response,
            format_tool_use,
            format_tool_result,
            format_error,
        ) = fns
        if action == "parse":
            return parse_request(kwargs.get("anthropic_request") or {})
        if action == "format_sse":
            return format_sse(delta=kwargs.get("delta", ""), model=kwargs.get("model", ""))
        if action == "format_response":
            return format_response(kwargs.get("chat_response") or {})
        if action == "format_tool_use":
            return format_tool_use(
                tool_id=kwargs.get("tool_id", ""),
                name=kwargs.get("name", ""),
                input=kwargs.get("input", {}),
            )
        if action == "format_tool_result":
            return format_tool_result(
                tool_use_id=kwargs.get("tool_use_id", ""),
                content=kwargs.get("content", ""),
                is_error=kwargs.get("is_error", False),
            )
        if action == "format_error":
            return format_error(
                error_type=kwargs.get("error_type", ""), message=kwargs.get("message", "")
            )
        raise ValueError(f"unknown action: {action}")

    def llm_merge(self, action, strategy=None, responses=None, providers=None, fail_at=None):
        merge, fallback = _load_llm_merge()
        if action == "merge":
            return merge(strategy=strategy or "concat", responses=responses or [])
        if action == "fallback":
            return fallback(providers=providers or [], fail_at=fail_at or [])
        raise ValueError(f"unknown action: {action}")

    def version(self, action, **kwargs):
        add, get, latest, parse_rating, parse_battle, swap = _load_versioning()
        if action == "add":
            return add(
                proposal_id=kwargs.get("proposal_id", ""),
                content=kwargs.get("content", ""),
                created_by=kwargs.get("created_by", ""),
            )
        if action == "get":
            return get(proposal_id=kwargs.get("proposal_id", ""))
        if action == "latest":
            return latest(proposal_id=kwargs.get("proposal_id", ""))
        if action == "parse_rating":
            return parse_rating(judge_response=kwargs.get("judge_response", ""))
        if action == "parse_battle":
            return parse_battle(judge_response=kwargs.get("judge_response", ""))
        if action == "swap_battle":
            return swap(
                judge_response=kwargs.get("judge_response", ""),
                judge_response_swapped=kwargs.get("judge_response_swapped", ""),
                response_a=kwargs.get("response_a", ""),
                response_b=kwargs.get("response_b", ""),
            )
        raise ValueError(f"unknown action: {action}")

    def worktree(self, action, repo_path=".", repo_path1=None, repo_path2=None):
        WorktreeManager = _load_worktree()
        mgr = WorktreeManager(cwd=repo_path)
        if action == "snapshot":
            return {"snapshot": mgr.snapshot()}
        if action == "list":
            return {"worktrees": mgr.list_worktrees()}
        if action == "diff":
            return {"diff": mgr.diff_snapshots(repo_path1 or repo_path, repo_path2 or repo_path)}
        raise ValueError(f"unknown action: {action}")
