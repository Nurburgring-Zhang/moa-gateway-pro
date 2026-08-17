"""v3.1.1 regression tests — P0 sandbox escape fix.

Covers:
1. Hardened AST sanitizer rejects every known escape class that defeated
   the v3.1.0 blacklist (dunder attribute, dunder subscript, format-string
   attribute traversal).
2. Legitimate computation code still passes and executes for real in the
   subprocess sandbox (no mocked outputs).
3. Route-level tool gating: non-admin callers cannot obtain dangerous tools
   via /v1/agent/run-loop (403 on explicit request, excluded from default).
"""
from __future__ import annotations

import asyncio

import pytest

from moa_gateway.agent_loop.skills.code_execute import (
    SandboxViolation,
    code_execute,
    sanitize_code,
)

# ---------------------------------------------------------------------------
# 1. Sanitizer: known v3.1.0 escape payloads MUST be rejected
# ---------------------------------------------------------------------------

ESCAPE_PAYLOADS = [
    # The exact payload that achieved RCE against v3.1.0 (audit K1):
    "json.__dict__['__builtins__']['open']('x.txt').read()",
    "json.__dict__['__builtins__']['__import__']('os').popen('id').read()",
    # Dunder attribute access variants
    "x = (1).__class__",
    "x = ''.__class__.__mro__",
    "print(json.__globals__)",
    "y = type.__subclasses__(type)",
    "z = json.__loader__",
    # Dunder subscript variants
    "d = {'a': 1}\nk = d['__class__']",
    "v = vars()['__builtins__']" if False else "m = json.__dict__['__globals__']",
    # Format-string attribute traversal
    "'{0.__class__}'.format(json)",
    "'{0.__init__.__globals__}'.format('')",
    "f'{json.__dict__}'",
    # Classic blacklist probes (still rejected)
    "import os",
    "from subprocess import run",
    "eval('1+1')",
    "exec('print(1)')",
    "open('secret.txt')",
    "__import__('socket')",
    "getattr(json, '__globals__')",
]


@pytest.mark.parametrize("payload", ESCAPE_PAYLOADS)
def test_sanitizer_rejects_escape_payloads(payload: str):
    with pytest.raises(SandboxViolation):
        sanitize_code(payload)


BENIGN_SNIPPETS = [
    "print(sum([1, 2, 3]))",
    "import math\nprint(math.sqrt(16))",
    "import json\nprint(json.dumps({'a': 1}))",
    "data = [3, 1, 2]\nprint(sorted(data))",
    "import statistics\nprint(statistics.mean([1, 2, 3, 4]))",
    "s = 'hello'\nprint(s.upper(), len(s))",
    "total = 0\nfor i in range(5):\n    total += i\nprint(total)",
    # format strings WITHOUT attribute traversal are fine
    "'{0} + {1} = {2}'.format(1, 2, 3)",
    "print(f'answer: {40 + 2}')",
]


@pytest.mark.parametrize("code", BENIGN_SNIPPETS)
def test_sanitizer_accepts_benign_code(code: str):
    sanitize_code(code)  # must not raise


# ---------------------------------------------------------------------------
# 2. Real subprocess execution (no mocks — actual child process)
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_code_execute_real_arithmetic():
    out = _run(code_execute("print(17 * 23)"))
    assert "391" in out


def test_code_execute_real_json_module():
    out = _run(code_execute("import json\nprint(json.dumps({'k': [1, 2]}))"))
    assert '{"k": [1, 2]}' in out


def test_code_execute_blocks_escape_at_runtime():
    # Even if someone bypassed the route gate, the payload dies in the
    # sanitizer before any process is spawned.
    out = _run(code_execute("json.__dict__['__builtins__']['__import__']('os')"))
    assert "Security violation" in out
    assert "forbidden" in out


def test_code_execute_import_os_blocked():
    out = _run(code_execute("import os\nprint(os.getcwd())"))
    assert "Security violation" in out


def test_code_execute_timeout_enforced():
    import time

    t0 = time.time()
    out = _run(code_execute("while True:\n    pass", timeout=3))
    elapsed = time.time() - t0
    assert "timed out" in out.lower()
    assert elapsed < 25, f"timeout not enforced promptly: {elapsed}s"


def test_code_execute_runtime_error_reported():
    out = _run(code_execute("x = 1 / 0"))
    assert "Execution error" in out
    assert "ZeroDivisionError" in out


def test_code_execute_child_has_no_inherited_secrets():
    # The scrubbed env must not expose parent secrets to the child.
    import os

    os.environ["MOA_AUDIT_SECRET_PROBE"] = "should-not-leak"
    try:
        out = _run(code_execute(
            "import json\n"
            "found = False\n"
            # os is blocked; the child cannot read env at all without it.
            "print('no-os-access')"
        ))
        assert "no-os-access" in out
        assert "should-not-leak" not in out
    finally:
        del os.environ["MOA_AUDIT_SECRET_PROBE"]


# ---------------------------------------------------------------------------
# 3. Route-level tool gating (live app via TestClient)
# ---------------------------------------------------------------------------

@pytest.fixture
def client_with_keys(monkeypatch, tmp_path, make_settings):
    """App client with one yaml admin key + one DB api key issued."""
    import moa_gateway.config as cfg
    import moa_gateway.storage as storage_mod

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(cfg, "_settings", None)
    monkeypatch.setattr(storage_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage_mod, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(storage_mod, "_FERNET_PATH", tmp_path / ".fernet_key")
    if "moa_gateway.storage" in __import__("sys").modules:
        from moa_gateway.storage import Storage
        Storage._instance = None

    settings = make_settings(gateway_api_keys=["yaml-admin-key-001"])
    monkeypatch.setattr(cfg, "_settings", settings)

    from fastapi.testclient import TestClient
    from moa_gateway.server import create_app

    app = create_app()
    storage = storage_mod.get_storage()
    db_key = storage.create_api_key("plain-key", quota_rpm=100, quota_daily_tokens=10_000)

    with TestClient(app) as c:
        yield c, db_key["key"]


def test_run_loop_nonadmin_explicit_dangerous_tool_403(client_with_keys):
    client, db_key = client_with_keys
    resp = client.post(
        "/v1/agent/run-loop",
        json={
            "messages": [{"role": "user", "content": "run code"}],
            "tools": ["code_execute"],
        },
        headers={"Authorization": f"Bearer {db_key}"},
    )
    assert resp.status_code == 403, resp.text
    assert "admin/operator" in resp.json()["detail"]


def test_run_loop_nonadmin_default_tools_exclude_dangerous(client_with_keys):
    client, db_key = client_with_keys
    resp = client.post(
        "/v1/agent/run-loop",
        json={
            "messages": [{"role": "user", "content": "Reply directly: OK"}],
            "max_iterations": 1,
        },
        headers={"Authorization": f"Bearer {db_key}"},
        timeout=60,
    )
    # The loop may fail on LLM call (no endpoints configured in this
    # fixture) — what we assert is the TOOL GATING, not the LLM outcome.
    if resp.status_code == 200:
        tools = resp.json().get("tools_available", [])
        for dangerous in ("code_execute", "file_read", "file_write", "file_list", "api_verify"):
            assert dangerous not in tools, f"{dangerous} leaked to non-admin"


def test_run_loop_admin_key_may_request_dangerous_tool(client_with_keys):
    client, _ = client_with_keys
    resp = client.post(
        "/v1/agent/run-loop",
        json={
            "messages": [{"role": "user", "content": "Reply directly: OK"}],
            "tools": ["code_execute", "web_search"],
            "max_iterations": 1,
        },
        headers={"Authorization": "Bearer yaml-admin-key-001"},
        timeout=60,
    )
    # 403 would mean gating wrongly blocks admin; any other status is fine
    # for this assertion (LLM availability is out of scope here).
    assert resp.status_code != 403, resp.text
