"""Tests for agent_loop module basic behavior."""
from __future__ import annotations

import pytest


def test_agent_loop_imports():
    """Verify core agent_loop modules can be imported."""
    from moa_gateway.agent_loop import base
    from moa_gateway.agent_loop import harness
    from moa_gateway.agent_loop import plan_execute_loop
    from moa_gateway.agent_loop import react_loop


def test_agent_loop_public_api():
    """Verify public API classes are accessible from package __init__."""
    from moa_gateway.agent_loop import (
        AgentContext,
        AgentHarness,
        AgentLoop,
        LoopResult,
        PlanExecuteLoop,
        ReActLoop,
        ToolCall,
        ToolExecutor,
        ToolResult,
    )

    assert AgentLoop is not None
    assert PlanExecuteLoop is not None
    assert ReActLoop is not None


def test_skills_imports():
    """Verify all skills can be imported."""
    from moa_gateway.agent_loop.skills import code_execute
    from moa_gateway.agent_loop.skills import api_verify
    from moa_gateway.agent_loop.skills import data_analysis
    from moa_gateway.agent_loop.skills import file_ops
    from moa_gateway.agent_loop.skills import web_search


def test_code_execute_sandbox_no_dangerous_builtins():
    """Verify dangerous builtins are removed from sandbox."""
    from moa_gateway.agent_loop.skills.code_execute import _ALLOWED_BUILTINS

    assert "getattr" not in _ALLOWED_BUILTINS
    assert "setattr" not in _ALLOWED_BUILTINS
    # type is allowed (AST layer blocks __subclasses__/__mro__ access)
    assert "type" in _ALLOWED_BUILTINS
    # hasattr should still be present (safe read-only introspection)
    assert "hasattr" in _ALLOWED_BUILTINS


def test_code_execute_sandbox_no_exec_eval():
    """Verify exec/eval are not exposed in sandbox builtins."""
    from moa_gateway.agent_loop.skills.code_execute import _ALLOWED_BUILTINS

    assert "exec" not in _ALLOWED_BUILTINS
    assert "eval" not in _ALLOWED_BUILTINS
    assert "compile" not in _ALLOWED_BUILTINS


def test_code_execute_sandbox_restricted_import():
    """Verify __import__ in sandbox is the restricted version (whitelist-only)."""
    from moa_gateway.agent_loop.skills.code_execute import (
        _ALLOWED_BUILTINS, _restricted_import, SandboxViolation,
    )

    # __import__ is present but restricted to whitelist
    assert "__import__" in _ALLOWED_BUILTINS
    assert _ALLOWED_BUILTINS["__import__"] is _restricted_import

    # Should allow whitelisted modules
    _restricted_import("math")

    # Should block non-whitelisted modules
    import pytest
    with pytest.raises(SandboxViolation):
        _restricted_import("os")
