"""SafetyService — wraps secret_scan, prompt_canary, tool_screening, output_wrapping, gate_l0, frozen_zone, grace_window, anthropic_compat, llm_merge, versioning, worktree.

Exposes:
  - secret_scan(path, fail_on)  # admin only
  - prompt_canary(action, prompt, response, canary, strategy)
  - tool_screening(tool_name, arguments)
  - output_wrapping(action, content, source, trust, max_length, wrapped)
  - frozen(action, path, zone, sentinel, reason, added_at)
  - grace(action, name, at, check_id)
  - anthropic_compat(action, ...)
  - llm_merge(action, strategy, responses, providers, fail_at)
  - version(action, ...)
  - worktree(action, repo_path, repo_path1, repo_path2)  # admin only
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


def _load_secret_scan():
    from ..capability.secret_scan import scan_path, should_block

    return scan_path, should_block


def _load_prompt_canary():
    # Audit fix: real API is CanaryDetector.inject / .check + generate_canary
    # (no module-level inject/check).
    from ..capability.prompt_canary import (
        CanaryDetector,
        CanaryStrategy,
        generate_canary,
    )

    return CanaryDetector, CanaryStrategy, generate_canary


def _load_tool_screening():
    from ..capability.tool_screening import ToolScreener, screen_input

    return screen_input, ToolScreener


def _load_output_wrapping():
    from ..capability.output_wrapping import (
        TrustLevel,
        needs_wrapping,
        safe_wrap,
        sanitize_for_prompt,
        unwrap_output,
        wrap_output,
    )

    return wrap_output, safe_wrap, sanitize_for_prompt, needs_wrapping, unwrap_output, TrustLevel


# Audit fix: frozen_zone exposes FrozenRegistry + can_modify / assert_modifiable
# / classify (no module-level is_frozen/add/is_evolvable/list_sentinels).
_frozen_registry = None


def _get_frozen_registry():
    global _frozen_registry
    from ..capability.frozen_zone import FrozenRegistry

    if _frozen_registry is None:
        _frozen_registry = FrozenRegistry()
    return _frozen_registry


def _load_frozen():
    from ..capability.frozen_zone import (
        FrozenEntry,
        FrozenRegistry,
        FrozenZoneError,
        Zone,
        assert_modifiable,
        can_modify,
        classify,
    )

    return FrozenRegistry, FrozenEntry, Zone, can_modify, assert_modifiable, classify, FrozenZoneError


# Audit fix: grace_window exposes CheckRegistry + grace_status (no module-level
# register/should_block/status/warnings).
_grace_registry = None


def _get_grace_registry():
    global _grace_registry
    from ..capability.grace_window import CheckRegistry

    if _grace_registry is None:
        _grace_registry = CheckRegistry()
    return _grace_registry


def _load_grace():
    from ..capability.grace_window import CheckRegistry, grace_status

    return CheckRegistry, grace_status


def _load_anthropic_compat():
    # Audit fix: real names carry the format_anthropic_* / parse_anthropic_*
    # prefixes.
    from ..capability.anthropic_compat import (
        format_anthropic_error,
        format_anthropic_response,
        format_anthropic_sse_chunk,
        format_anthropic_tool_result,
        format_anthropic_tool_use,
        parse_anthropic_request,
    )

    return (
        parse_anthropic_request,
        format_anthropic_sse_chunk,
        format_anthropic_response,
        format_anthropic_tool_use,
        format_anthropic_tool_result,
        format_anthropic_error,
    )


def _load_llm_merge():
    # Audit fix: real API is merge_responses(list[LLMResponse], MergeStrategy)
    # + FallbackChain (no module-level merge/fallback).
    from ..capability.llm_merge import (
        AllProvidersFailedError,
        FallbackChain,
        LLMResponse,
        MergeStrategy,
        merge_responses,
    )

    return merge_responses, FallbackChain, LLMResponse, MergeStrategy, AllProvidersFailedError


# Audit fix: versioning exposes the VersionStore class (no module-level
# add/get/latest).
_version_store = None


def _get_version_store():
    global _version_store
    from ..capability.versioning import VersionStore

    if _version_store is None:
        _version_store = VersionStore()
    return _version_store


def _load_versioning():
    from ..capability.versioning import (
        VersionStore,
        parse_battle,
        parse_rating,
        swap_positions_battle,
    )

    return VersionStore, parse_rating, parse_battle, swap_positions_battle


def _load_worktree():
    # Audit fix: snapshot / diff_snapshots / is_clean are module-level;
    # WorktreeManager takes repo_path (not cwd) and only covers worktree CRUD.
    from ..capability.worktree import (
        WorktreeManager,
        diff_snapshots,
        is_clean,
        snapshot,
    )

    return WorktreeManager, snapshot, diff_snapshots, is_clean


def _finding_to_dict(f) -> dict:
    d = asdict(f)
    if hasattr(d.get("risk"), "value"):
        d["risk"] = d["risk"].value
    return d


class SafetyService(ServiceBase):
    name = "safety"
    description = "安全 / 兼容性 / 工具筛选 / 输出包装"

    def _register_methods(self):
        self._methods["secret_scan"] = ServiceMethod(
            name="secret_scan",
            description="扫描路径中的 secret (admin only)",
            func=self.secret_scan,
            input_required=["path"],
            input_optional=["fail_on"],
        )
        self._methods["prompt_canary"] = ServiceMethod(
            name="prompt_canary",
            description="prompt canary (inject / check / generate)",
            func=self.prompt_canary,
            input_required=["action"],
            input_optional=["prompt", "response", "canary", "strategy", "length"],
        )
        self._methods["tool_screening"] = ServiceMethod(
            name="tool_screening",
            description="筛选工具调用(防 rm -rf / 等危险)",
            func=self.tool_screening,
            input_required=["tool_name", "arguments"],
        )
        self._methods["output_wrapping"] = ServiceMethod(
            name="output_wrapping",
            description="输出包装(sanitize/wrap/unwrap/needs_wrapping; trust: trusted|semi|untrusted)",
            func=self.output_wrapping,
            input_required=["action"],
            input_optional=["content", "source", "trust", "max_length", "wrapped"],
        )
        self._methods["frozen"] = ServiceMethod(
            name="frozen",
            description=(
                "frozen zone 管理 (FrozenRegistry): is_frozen/add/is_evolvable/can_modify/"
                "assert_modifiable/classify/list_sentinels; zone: frozen-canonical|frozen-safety|"
                "evolvable-tuning|evolvable-experimental"
            ),
            func=self.frozen,
            input_required=["action"],
        )
        self._methods["grace"] = ServiceMethod(
            name="grace",
            description="grace window (CheckRegistry): register/record_fail/record_pass/should_block/status/warnings",
            func=self.grace,
            input_required=["action"],
            input_optional=["name", "at", "check_id"],
        )
        self._methods["anthropic_compat"] = ServiceMethod(
            name="anthropic_compat",
            description="Anthropic API 兼容(parse/format_sse/format_response/format_tool_use/format_tool_result/format_error)",
            func=self.anthropic_compat,
            input_required=["action"],
        )
        self._methods["llm_merge"] = ServiceMethod(
            name="llm_merge",
            description=(
                "LLM 合并: merge 走 merge_responses (strategy: CONCAT|DEDUP|VOTE|WEIGHTED|FIRST_SUCCESS); "
                "fallback 走 FallbackChain.execute, fail_at 内的 provider 模拟失败"
            ),
            func=self.llm_merge,
            input_required=["action"],
            input_optional=["strategy", "responses", "providers", "fail_at"],
        )
        self._methods["version"] = ServiceMethod(
            name="version",
            description="版本管理(VersionStore add/get/latest + parse_rating/parse_battle/swap_battle)",
            func=self.version,
            input_required=["action"],
        )
        self._methods["worktree"] = ServiceMethod(
            name="worktree",
            description="worktree 管理 (admin only): snapshot/list/diff",
            func=self.worktree,
            input_required=["action"],
            input_optional=["repo_path", "repo_path1", "repo_path2"],
        )

    def secret_scan(self, path, fail_on=99):
        # v3.1.1 audit P1-1: sensitive filesystem read — admin/operator only.
        from .base import dispatch_ctx

        role = (dispatch_ctx.get() or {}).get("role", "")
        if role not in ("admin", "operator"):
            raise PermissionError(
                f"secret_scan requires admin/operator role (caller role={role or 'unknown'})"
            )

        scan_path, should_block = _load_secret_scan()
        import os
        from pathlib import Path

        # Round-1 (P0-7) + v3.1.1: path containment via commonpath (AGENTS.md
        # rule 6 — startswith is bypassable by prefix-adjacent paths).
        allowed = [
            Path.cwd(),
            Path.cwd() / "moa_gateway",
            Path.cwd() / "scripts",
            Path.cwd() / "src",
            Path.home() / ".moa-gateway",
        ]
        target = Path(path).resolve()
        contained = False
        for a in allowed:
            ar = a.resolve()
            try:
                if os.path.commonpath([str(target), str(ar)]) == str(ar):
                    contained = True
                    break
            except ValueError:
                continue
        if not contained:
            raise ValueError(f"path not in allowlist: {target}")

        result = scan_path(target)
        # v3.1.1 audit P1-1: to_dict() redacts raw secret material at source;
        # asdict() would have leaked the plaintext match field.
        out = result.to_dict()
        out["blocked"] = should_block(result, fail_on=int(fail_on))
        return {"path": str(target), "scan": out}

    def prompt_canary(self, action, prompt=None, response=None, canary=None, strategy="suffix", length=16):
        # Audit fix: drive CanaryDetector (inject returns (prompt, canary)).
        CanaryDetector, CanaryStrategy, generate_canary = _load_prompt_canary()
        try:
            strat = CanaryStrategy(strategy)
        except ValueError as e:
            valid = [s.value for s in CanaryStrategy]
            raise ValueError(f"unknown strategy: {strategy!r}, expected one of {valid}") from e
        if action == "inject":
            detector = CanaryDetector(strategy=strat, canary_length=int(length))
            injected, canary_token = detector.inject(prompt or "")
            return {"prompt": injected, "canary": canary_token, "strategy": strategy}
        if action == "check":
            detector = CanaryDetector(strategy=strat, canary_length=int(length))
            return detector.check(response or "", canary or "")
        if action == "generate":
            return {"canary": generate_canary(strategy=strat, length=int(length))}
        raise ValueError(f"unknown action: {action}")

    def tool_screening(self, tool_name, arguments):
        screen_input, ToolScreener = _load_tool_screening()
        screener = ToolScreener()
        findings = screener.screen(tool_name=tool_name, arguments=arguments)
        return {
            "findings": [_finding_to_dict(f) for f in findings],
            "risk": screener.classify(findings).value,
            "should_block": screener.should_block(findings),
        }

    def output_wrapping(
        self, action, content=None, source=None, trust=None, max_length=None, wrapped=None
    ):
        # Audit fix: trust must be a TrustLevel enum (functions read trust.value).
        wrap_output, safe_wrap, sanitize_for_prompt, needs_wrapping, unwrap_output, TrustLevel = (
            _load_output_wrapping()
        )
        try:
            trust_level = TrustLevel(trust or "untrusted")
        except ValueError as e:
            valid = [t.value for t in TrustLevel]
            raise ValueError(f"unknown trust: {trust!r}, expected one of {valid}") from e
        kw = {}
        if max_length is not None:
            kw["max_length"] = int(max_length)
        if action == "wrap":
            return {
                "wrapped": safe_wrap(
                    content=content or "", source=source or "unknown", trust=trust_level, **kw
                )
            }
        if action == "wrap_plain":
            return {
                "wrapped": wrap_output(
                    content=content or "", source=source or "unknown", trust=trust_level, **kw
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
        # Audit fix: drive FrozenRegistry + module-level can_modify /
        # assert_modifiable / classify.
        _FrozenRegistry, FrozenEntry, Zone, can_modify, assert_modifiable, classify, FrozenZoneError = (
            _load_frozen()
        )
        registry = _get_frozen_registry()

        def _zone():
            try:
                return Zone(zone or "frozen-canonical")
            except ValueError as e:
                valid = [z.value for z in Zone]
                raise ValueError(f"unknown zone: {zone!r}, expected one of {valid}") from e

        if action == "is_frozen":
            return {"frozen": registry.is_frozen(path=path or "")}
        if action == "add":
            entry = FrozenEntry(
                path=str(path or ""),
                zone=_zone(),
                sentinel=str(sentinel or ""),
                reason=str(reason or ""),
                added_at=float(added_at) if added_at else time.time(),
            )
            registry.add(entry)
            return {"added": True, "path": entry.path, "zone": entry.zone.value}
        if action == "is_evolvable":
            return {"evolvable": registry.is_evolvable(path=path or "")}
        if action == "can_modify":
            return {"can_modify": can_modify(path=path or "", zone=_zone())}
        if action == "assert_modifiable":
            try:
                assert_modifiable(path=path or "", registry=registry)
                return {"ok": True}
            except FrozenZoneError as e:
                return {"ok": False, "error": str(e)}
        if action == "classify":
            return {"class": classify(_zone())}
        if action == "list_sentinels":
            return {
                "sentinels": [
                    {
                        "path": e.path,
                        "sentinel": e.sentinel,
                        "zone": e.zone.value,
                        "reason": e.reason,
                    }
                    for e in registry.list_entries()
                ]
            }
        raise ValueError(f"unknown action: {action}")

    def grace(self, action, name=None, at=None, check_id=None):
        # Audit fix: drive CheckRegistry + grace_status.
        _CheckRegistry, grace_status = _load_grace()
        registry = _get_grace_registry()
        ts = float(at) if at else None
        if action == "register":
            return {"check_id": registry.register(name=name or "")}
        if action == "record_fail":
            registry.record_fail(check_id or "", at=ts)
            return {"recorded": True, "check_id": check_id}
        if action == "record_pass":
            registry.record_pass(check_id or "")
            return {"recorded": True, "check_id": check_id}
        if action == "should_block":
            return {"block": registry.should_block(check_id=check_id or "", at=ts)}
        if action == "status":
            return {"status": grace_status(check_id or "", registry, at=ts)}
        if action == "warnings":
            return {"warnings": [asdict(cr) for cr in registry.get_warnings(at=ts)]}
        raise ValueError(f"unknown action: {action}")

    def anthropic_compat(self, action, **kwargs):
        # Audit fix: real function names carry the *_anthropic_* prefixes.
        (
            parse_request,
            format_sse,
            format_response,
            format_tool_use,
            format_tool_result,
            format_error,
        ) = _load_anthropic_compat()
        if action == "parse":
            return parse_request(kwargs.get("anthropic_request") or {})
        if action == "format_sse":
            return {
                "chunk": format_sse(
                    delta=kwargs.get("delta", ""),
                    model=kwargs.get("model", ""),
                    stop_reason=kwargs.get("stop_reason"),
                )
            }
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
            openai_error = kwargs.get("openai_error") or {
                "message": kwargs.get("message", ""),
                "type": kwargs.get("error_type", ""),
            }
            return format_error(openai_error, status=int(kwargs.get("status", 400)))
        raise ValueError(f"unknown action: {action}")

    def llm_merge(self, action, strategy=None, responses=None, providers=None, fail_at=None):
        # Audit fix: drive merge_responses / FallbackChain.
        merge_responses, FallbackChain, LLMResponse, MergeStrategy, AllProvidersFailedError = (
            _load_llm_merge()
        )
        if action == "merge":
            try:
                strat = MergeStrategy[strategy or "CONCAT"]
            except KeyError as e:
                valid = [m.name for m in MergeStrategy]
                raise ValueError(f"unknown strategy: {strategy!r}, expected one of {valid}") from e
            resp_objs = []
            for r in responses or []:
                if isinstance(r, LLMResponse):
                    resp_objs.append(r)
                elif isinstance(r, dict):
                    valid = {k: v for k, v in r.items() if k in LLMResponse.__dataclass_fields__}
                    resp_objs.append(LLMResponse(**valid))
                else:
                    raise ValueError("each response must be a dict")
            result = merge_responses(resp_objs, strat)
            return {
                "text": result.text,
                "sources": result.sources,
                "strategy": result.strategy.name,
                "total_tokens": result.total_tokens,
                "total_cost_usd": result.total_cost_usd,
                "confidence": result.confidence,
            }
        if action == "fallback":
            chain = FallbackChain([str(p) for p in (providers or [])])
            fail_set = {str(p) for p in (fail_at or [])}

            def call_fn(provider: str) -> LLMResponse:
                if provider in fail_set:
                    raise RuntimeError(f"simulated failure for provider {provider}")
                return LLMResponse(
                    source=provider,
                    text=f"[{provider}] response",
                    tokens=10,
                    latency_ms=1.0,
                    cost_usd=0.001,
                    confidence=0.9,
                )

            try:
                resp = chain.execute(call_fn)
            except AllProvidersFailedError as e:
                return {
                    "ok": False,
                    "all_failed": True,
                    "providers_tried": list(e.providers),
                    "errors": [str(err) for err in (e.errors or [])],
                }
            return {
                "ok": True,
                "provider_order": chain.providers,
                "response": {
                    "source": resp.source,
                    "text": resp.text,
                    "tokens": resp.tokens,
                    "latency_ms": resp.latency_ms,
                    "cost_usd": resp.cost_usd,
                    "confidence": resp.confidence,
                },
            }
        raise ValueError(f"unknown action: {action}")

    def version(self, action, **kwargs):
        # Audit fix: drive VersionStore (add_version/get_chain/latest) +
        # parse_rating / parse_battle / swap_positions_battle.
        _VersionStore, parse_rating, parse_battle, swap_positions_battle = _load_versioning()
        store = _get_version_store()
        if action == "add":
            version_id = store.add_version(
                proposal_id=kwargs.get("proposal_id", ""),
                content=kwargs.get("content", ""),
                parent=kwargs.get("parent"),
                critique=kwargs.get("critique"),
                improvement=kwargs.get("improvement"),
                created_by=kwargs.get("created_by", "system"),
            )
            return {"version_id": version_id, "proposal_id": kwargs.get("proposal_id", "")}
        if action == "get":
            proposal_id = kwargs.get("proposal_id", "")
            version_id = kwargs.get("version_id")
            if version_id:
                v = store.get_version(proposal_id, version_id)
                return {"version": asdict(v) if v else None}
            chain = store.get_chain(proposal_id)
            return {"proposal_id": proposal_id, "versions": [asdict(v) for v in chain.versions]}
        if action == "latest":
            v = store.latest(kwargs.get("proposal_id", ""))
            return {"version": asdict(v) if v else None}
        if action == "parse_rating":
            return {"rating": parse_rating(judge_response=kwargs.get("judge_response", ""))}
        if action == "parse_battle":
            winner, confidence = parse_battle(judge_response=kwargs.get("judge_response", ""))
            return {"winner": winner, "confidence": confidence}
        if action == "swap_battle":
            response_a = kwargs.get("response_a", "")
            response_b = kwargs.get("response_b", "")
            judge_response = kwargs.get("judge_response", "")
            judge_response_swapped = kwargs.get("judge_response_swapped", judge_response)

            def judge_fn(first: str, second: str) -> str:
                # Deterministic judge: returns the original verdict when the
                # responses appear in their original order, the swapped verdict
                # otherwise — exactly what a position-bias probe needs.
                if first == response_a and second == response_b:
                    return judge_response
                return judge_response_swapped

            winner = swap_positions_battle(response_a, response_b, judge_fn)
            return {"winner": winner}
        raise ValueError(f"unknown action: {action}")

    def worktree(self, action, repo_path=".", repo_path1=None, repo_path2=None):
        # Audit fix: WorktreeManager(repo_path=...) — snapshot/diff_snapshots
        # are module-level functions over WorktreeSnapshot objects.
        WorktreeManager, snapshot, diff_snapshots, is_clean = _load_worktree()
        if action == "snapshot":
            snap = snapshot(repo_path)
            out = asdict(snap)
            out["clean"] = is_clean(snap)
            return {"snapshot": out}
        if action == "list":
            mgr = WorktreeManager(repo_path=repo_path)
            return {"worktrees": [asdict(w) for w in mgr.list_worktrees()]}
        if action == "diff":
            s1 = snapshot(repo_path1 or repo_path)
            s2 = snapshot(repo_path2 or repo_path)
            return {"diff": diff_snapshots(s1, s2)}
        raise ValueError(f"unknown action: {action}")
