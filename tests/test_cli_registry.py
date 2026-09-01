"""外部 CLI 注册表 + 三通道真实化测试 (v3.1.1 审计整改).

覆盖:
1. CLIRegistry CRUD + SQLite 持久化
2. 可执行文件白名单拒绝 (rm / cmd / powershell 等)
3. 真实执行 python --version / -c (subprocess.run, 无 shell)
4. argv 占位符注入安全 ("; rm -rf" 等只作为字面量参数)
5. 超时杀进程 + 输出上限截断 + env 机密清洗
6. cwd 沙箱限制 + allowed_dirs 白名单
7. execute-batch 多路并发聚合 + 部分失败隔离
8. 三通道链真实执行 (CLIChannel 子进程 / APIChannel model_pool / SubagentChannel 回环)
9. RBAC: 读 require_api_key,写/执行 require_admin
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from moa_gateway.capability.cli_registry import (
    CLIExecResult,
    CLIRegistry,
    CLIRegistryError,
    executable_basename,
    render_argv,
    scrubbed_env,
)

PY = sys.executable  # 绝对路径,basename=python → 命中默认白名单


# ============ fixtures ============


@pytest.fixture(autouse=True)
def _isolate_cli_registry(tmp_path, monkeypatch):
    """每个测试独立: ROOT_DIR 指到 tmp,重置 registry 单例。"""
    import moa_gateway.capability.cli_registry as cr
    import moa_gateway.config as cfg

    monkeypatch.setattr(cfg, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(cr, "_registry", None)
    yield
    cr._registry = None


@pytest.fixture
def registry(storage_instance):
    return CLIRegistry(storage=storage_instance)


def _register_py_echo(registry: CLIRegistry, name: str = "py-echo", **kw) -> dict:
    """注册一个把 {payload} 原样打印的 python 工具。"""
    return registry.register(
        name,
        [PY, "-c", "import sys; sys.stdout.write(sys.argv[1])", "{payload}"],
        **kw,
    )


# ============ 1. argv 渲染 / 白名单 单元测试 ============


class TestArgvRendering:
    def test_render_replaces_placeholder_as_single_element(self):
        argv = render_argv(["python", "-c", "print({x})"], {"x": "hello world"})
        assert argv == ["python", "-c", "print(hello world)"]

    def test_render_missing_param_rejected(self):
        with pytest.raises(CLIRegistryError, match="missing param"):
            render_argv(["python", "{nope}"], {})

    def test_render_injection_payload_stays_literal(self):
        evil = '"; rm -rf /tmp/victim; echo pwned; $(whoami) & '
        argv = render_argv(["python", "-c", "x", "{payload}"], {"payload": evil})
        # 注入串必须原样保留为单个 argv 元素 — 没有被拆分/解释
        assert argv[-1] == evil
        assert len(argv) == 4

    def test_render_rejects_container_params(self):
        with pytest.raises(CLIRegistryError, match="scalar"):
            render_argv(["python", "{x}"], {"x": ["a", "b"]})

    def test_executable_basename_normalization(self):
        assert executable_basename("python") == "python"
        assert executable_basename("C:\\Python312\\python.exe") == "python"
        assert executable_basename("/usr/bin/python3") == "python3"
        assert executable_basename("GIT.CMD") == "git"


class TestEnvScrubbing:
    def test_secrets_never_inherited(self, monkeypatch):
        monkeypatch.setenv("MOA_ADMIN_PASSWORD", "super-secret-pw")
        monkeypatch.setenv("MOA_GATEWAY_KEY", "mgw-secret")
        env = scrubbed_env()
        assert "MOA_ADMIN_PASSWORD" not in env
        assert "MOA_GATEWAY_KEY" not in env
        assert not any(k.upper().startswith("MOA_") for k in env)

    def test_path_preserved(self):
        env = scrubbed_env()
        assert env.get("PATH"), "PATH must survive for executable lookup"

    def test_env_extra_secret_keys_refused(self):
        with pytest.raises(CLIRegistryError, match="secret"):
            scrubbed_env({"MY_API_KEY": "x"})
        with pytest.raises(CLIRegistryError, match="secret"):
            scrubbed_env({"DB_PASSWORD": "x"})

    def test_env_extra_benign_keys_allowed(self):
        env = scrubbed_env({"MY_FLAG": "1"})
        assert env["MY_FLAG"] == "1"


# ============ 2. 注册 CRUD + 持久化 ============


class TestRegistryCRUD:
    def test_register_and_get(self, registry):
        spec = _register_py_echo(registry, description="echo tool")
        assert spec["name"] == "py-echo"
        got = registry.get("py-echo")
        assert got is not None
        assert got["argv_template"] == spec["argv_template"]
        assert got["description"] == "echo tool"
        assert got["timeout_s"] > 0
        assert got["max_output_bytes"] > 0

    def test_list(self, registry):
        _register_py_echo(registry, "tool-a")
        _register_py_echo(registry, "tool-b")
        names = [t["name"] for t in registry.list()]
        assert names == ["tool-a", "tool-b"]

    def test_duplicate_register_rejected(self, registry):
        _register_py_echo(registry)
        with pytest.raises(CLIRegistryError, match="already exists"):
            _register_py_echo(registry)

    def test_update_fields(self, registry):
        _register_py_echo(registry, timeout_s=10.0)
        updated = registry.update("py-echo", timeout_s=20.0, description="new desc")
        assert updated["timeout_s"] == 20.0
        assert updated["description"] == "new desc"
        # 未更新字段保持
        assert updated["argv_template"][0] == PY

    def test_update_invalid_rolls_back(self, registry):
        _register_py_echo(registry, timeout_s=10.0)
        with pytest.raises(CLIRegistryError):
            registry.update("py-echo", timeout_s=99999.0)  # 超 max_timeout_s
        # 原记录完好
        assert registry.get("py-echo")["timeout_s"] == 10.0

    def test_update_unknown_tool(self, registry):
        with pytest.raises(CLIRegistryError, match="unknown cli tool"):
            registry.update("ghost", description="x")

    def test_delete(self, registry):
        _register_py_echo(registry)
        assert registry.delete("py-echo") is True
        assert registry.get("py-echo") is None
        assert registry.delete("py-echo") is False

    def test_persistence_across_instances(self, storage_instance):
        r1 = CLIRegistry(storage=storage_instance)
        _register_py_echo(r1)
        r2 = CLIRegistry(storage=storage_instance)  # 新实例,同一 DB
        assert r2.get("py-echo") is not None

    def test_invalid_name_rejected(self, registry):
        with pytest.raises(CLIRegistryError, match="invalid tool name"):
            registry.register("../evil", [PY, "--version"])

    def test_env_extra_with_secret_refused_at_register(self, registry):
        with pytest.raises(CLIRegistryError, match="secret"):
            _register_py_echo(registry, env_extra={"AWS_SECRET_KEY": "x"})


# ============ 3. 白名单拒绝 ============


class TestExecutableAllowlist:
    @pytest.mark.parametrize("bad", ["rm", "cmd", "cmd.exe", "powershell", "sh", "bash", "mkfs"])
    def test_register_blocked_executable_refused(self, registry, bad):
        with pytest.raises(CLIRegistryError, match="not in cli.allowed_executables"):
            registry.register(f"bad-{bad.replace('.', '-')}", [bad, "-rf", "/"])

    def test_register_path_disguise_refused(self, registry, tmp_path):
        # 拿不存在的路径伪装白名单名字 → 拒绝
        fake = str(tmp_path / "not-there" / "python")
        with pytest.raises(CLIRegistryError):
            registry.register("disguise", [fake, "--version"])

    def test_placeholder_in_argv0_refused(self, registry):
        with pytest.raises(CLIRegistryError, match="fixed executable"):
            registry.register("sneaky", ["{prog}", "--version"])

    def test_admin_can_extend_allowlist(self, storage_instance, monkeypatch):
        import moa_gateway.config as cfg

        settings = cfg.get_settings()
        settings.cli.allowed_executables = list(settings.cli.allowed_executables) + ["rg"]
        monkeypatch.setattr(cfg, "get_settings", lambda: settings)
        reg = CLIRegistry(storage=storage_instance)
        spec = reg.register("search", ["rg", "--version"])
        assert spec["argv_template"][0] == "rg"


# ============ 4. 真实执行 ============


class TestRealExecution:
    def test_python_version_real(self, registry):
        registry.register("pyver", [PY, "--version"])
        res = registry.execute("pyver")
        assert isinstance(res, CLIExecResult)
        assert res.ok is True
        assert res.exit_code == 0
        text = (res.stdout + res.stderr).strip()
        assert text.startswith("Python 3"), f"real python version expected, got {text!r}"
        assert res.latency_ms >= 0
        assert res.argv == [PY, "--version"]

    def test_placeholder_param_real(self, registry):
        _register_py_echo(registry)
        res = registry.execute("py-echo", {"payload": "hello-real-world"})
        assert res.ok is True
        assert res.stdout == "hello-real-world"

    def test_injection_payload_executed_literally(self, registry, tmp_path):
        """占位符里塞 shell 注入串:无 shell=True,只能被当字面量打印。"""
        _register_py_echo(registry)
        victim = tmp_path / "victim.txt"
        victim.write_text("data", encoding="utf-8")
        evil = f'"; rm -rf {tmp_path}; echo pwned; & whoami'
        res = registry.execute("py-echo", {"payload": evil})
        assert res.ok is True
        assert res.stdout == evil  # 原样回显 = 没被 shell 解释
        assert victim.exists(), "injection must not touch the filesystem"

    def test_nonzero_exit_classified_cli(self, registry):
        registry.register("fail1", [PY, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
        res = registry.execute("fail1")
        assert res.ok is False
        assert res.exit_code == 3
        assert "boom" in res.stderr
        assert res.error_kind == "cli"

    def test_auth_like_stderr_classified_auth(self, registry):
        registry.register(
            "authfail",
            [PY, "-c", "import sys; sys.stderr.write('401 unauthorized'); sys.exit(1)"],
        )
        res = registry.execute("authfail")
        assert res.ok is False
        assert res.error_kind == "auth"

    def test_empty_output_classified_empty(self, registry):
        registry.register("silent", [PY, "-c", "pass"])
        res = registry.execute("silent")
        assert res.ok is False
        assert res.exit_code == 0
        assert res.error_kind == "empty"

    def test_timeout_kills_child(self, registry):
        registry.register("sleeper", [PY, "-c", "import time; time.sleep(30)"], timeout_s=30)
        t0 = time.perf_counter()
        res = registry.execute("sleeper", timeout_s=1)
        elapsed = time.perf_counter() - t0
        assert res.ok is False
        assert res.timed_out is True
        assert res.error_kind == "timeout"
        assert res.exit_code == -1
        assert elapsed < 10, "child must be killed near the timeout, not run to completion"

    def test_output_cap_truncates(self, registry):
        registry.register(
            "bigout",
            [PY, "-c", "print('A' * 5000)"],
            max_output_bytes=100,
        )
        res = registry.execute("bigout")
        assert res.ok is True
        assert res.truncated is True
        assert len(res.stdout.encode()) <= 100

    def test_env_scrubbed_in_child(self, registry, monkeypatch):
        monkeypatch.setenv("MOA_ADMIN_PASSWORD", "leak-me-if-you-can")
        registry.register(
            "envprobe",
            [PY, "-c", "import os; print(os.environ.get('MOA_ADMIN_PASSWORD', '<absent>'))"],
        )
        res = registry.execute("envprobe")
        assert res.ok is True
        assert res.stdout.strip() == "<absent>"

    def test_env_extra_reaches_child(self, registry):
        registry.register(
            "envflag",
            [PY, "-c", "import os; print(os.environ.get('MY_TOOL_FLAG', '<absent>'))"],
            env_extra={"MY_TOOL_FLAG": "on"},
        )
        res = registry.execute("envflag")
        assert res.stdout.strip() == "on"

    def test_default_cwd_is_sandbox(self, registry):
        registry.register("pwd", [PY, "-c", "import os; print(os.getcwd())"])
        res = registry.execute("pwd")
        assert res.ok is True
        sandbox = registry.sandbox_dir()
        assert Path(res.stdout.strip()) == sandbox
        assert sandbox.is_dir()

    def test_cwd_outside_allowlist_refused(self, registry, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(CLIRegistryError, match="cwd not in allowlist"):
            registry.register("escape", [PY, "--version"], cwd=str(outside))

    def test_cwd_whitelisted_via_settings(self, storage_instance, tmp_path, monkeypatch):
        import moa_gateway.config as cfg

        workdir = tmp_path / "allowed-work"
        workdir.mkdir()
        settings = cfg.get_settings()
        settings.cli.allowed_dirs = [str(workdir)]
        monkeypatch.setattr(cfg, "get_settings", lambda: settings)
        reg = CLIRegistry(storage=storage_instance)
        reg.register("pwd2", [PY, "-c", "import os; print(os.getcwd())"], cwd=str(workdir))
        res = reg.execute("pwd2")
        assert res.ok is True
        assert Path(res.stdout.strip()) == workdir.resolve()

    def test_unknown_tool_execute_raises(self, registry):
        with pytest.raises(CLIRegistryError, match="unknown cli tool"):
            registry.execute("ghost")

    def test_execute_argv_inline(self, registry):
        res = registry.execute_argv([PY, "-c", "print('inline-ok')"])
        assert res.ok is True
        assert res.stdout.strip() == "inline-ok"

    def test_execute_argv_inline_allowlist_enforced(self, registry):
        with pytest.raises(CLIRegistryError, match="not in cli.allowed_executables"):
            registry.execute_argv(["rm", "-rf", "/"])


# ============ 5. batch 并发聚合 ============


class TestBatchExecution:
    @pytest.mark.anyio
    async def test_batch_concurrent_aggregation(self, registry):
        """两路各 sleep 0.8s:并发时墙钟应明显小于两路 latency 之和 (串行)。"""
        registry.register("slow-a", [PY, "-c", "import time; time.sleep(0.8); print('A')"])
        registry.register("slow-b", [PY, "-c", "import time; time.sleep(0.8); print('B')"])
        t0 = time.perf_counter()
        results = await registry.execute_batch([{"name": "slow-a"}, {"name": "slow-b"}])
        wall = time.perf_counter() - t0
        assert len(results) == 2
        assert results[0]["ok"] is True and results[0]["stdout"].strip() == "A"
        assert results[1]["ok"] is True and results[1]["stdout"].strip() == "B"
        for r in results:
            assert r["exit_code"] == 0
            assert r["latency_ms"] >= 700  # 每路真实耗时证据
        serial_sum_ms = results[0]["latency_ms"] + results[1]["latency_ms"]
        # 并发判据 (自归一化,不依赖机器绝对速度): 墙钟 < 两路耗时之和的 80%
        assert wall * 1000 < serial_sum_ms * 0.8, (
            f"batch must run concurrently: wall={wall:.2f}s sum={serial_sum_ms}ms"
        )

    @pytest.mark.anyio
    async def test_batch_partial_failure_isolated(self, registry):
        registry.register("good", [PY, "-c", "print('good')"])
        registry.register("bad", [PY, "-c", "import sys; sys.exit(7)"])
        results = await registry.execute_batch([{"name": "good"}, {"name": "bad"}])
        assert results[0]["ok"] is True
        assert results[1]["ok"] is False
        assert results[1]["exit_code"] == 7
        assert results[1]["error_kind"] == "cli"

    @pytest.mark.anyio
    async def test_batch_unknown_tool_isolated(self, registry):
        registry.register("good2", [PY, "-c", "print('ok')"])
        results = await registry.execute_batch(
            [{"name": "good2"}, {"name": "ghost"}, {"name": "good2", "params": {}}]
        )
        assert results[0]["ok"] is True
        assert results[1]["ok"] is False
        assert "unknown cli tool" in results[1]["error"]
        assert results[2]["ok"] is True

    @pytest.mark.anyio
    async def test_batch_per_item_params_and_timeout(self, registry):
        _register_py_echo(registry, "echo-batch")
        registry.register("slow-c", [PY, "-c", "import time; time.sleep(10)"])
        results = await registry.execute_batch(
            [
                {"name": "echo-batch", "params": {"payload": "p1"}},
                {"name": "slow-c", "timeout_s": 1},
            ]
        )
        assert results[0]["ok"] is True and results[0]["stdout"] == "p1"
        assert results[1]["ok"] is False and results[1]["timed_out"] is True


# ============ 6. 三通道真实执行 ============


class FakeEndpoint:
    def __init__(self, id: str):
        self.id = id


class FakePool:
    """APIChannel 注入用最小 fake — 只模拟端点选择,执行路径仍是真实的。"""

    def __init__(self, endpoints=None, content="fake-api-answer"):
        self.endpoints = endpoints or {}
        self.content = content
        self.calls = []

    def available_endpoints(self, **kw):
        return list(self.endpoints.values())

    async def call(self, endpoint_id, messages, **kw):
        self.calls.append((endpoint_id, messages, kw))

        class _Resp:
            content = self.content

        return _Resp()


class TestChannelsReal:
    @pytest.mark.anyio
    async def test_cli_channel_real_subprocess(self, registry):
        from moa_gateway.capability.channels import CLIChannel, ChannelType

        _register_py_echo(registry, "ch-tool")
        ch = CLIChannel(tool="ch-tool", registry=registry)
        res = await ch.execute("query-text", params={"payload": "from-channel"})
        assert res.channel == ChannelType.CLI
        assert res.success is True
        assert res.output == "from-channel"
        assert res.latency_ms >= 0

    @pytest.mark.anyio
    async def test_cli_channel_query_placeholder(self, registry):
        from moa_gateway.capability.channels import CLIChannel

        registry.register(
            "q-echo",
            [PY, "-c", "import sys; sys.stdout.write(sys.argv[1])", "{query}"],
        )
        ch = CLIChannel(tool="q-echo", registry=registry)
        res = await ch.execute("the-query-itself")
        assert res.success is True
        assert res.output == "the-query-itself"

    @pytest.mark.anyio
    async def test_cli_channel_unconfigured_fails_honestly(self):
        from moa_gateway.capability.channels import CLIChannel

        ch = CLIChannel()
        res = await ch.execute("anything")
        assert res.success is False
        assert res.error.startswith("cli:")

    @pytest.mark.anyio
    async def test_cli_channel_exit_nonzero_error_kind(self, registry):
        from moa_gateway.capability.channels import CLIChannel

        registry.register("ch-fail", [PY, "-c", "import sys; sys.exit(2)"])
        ch = CLIChannel(tool="ch-fail", registry=registry)
        res = await ch.execute("x")
        assert res.success is False
        assert res.error.startswith("cli:"), res.error

    @pytest.mark.anyio
    async def test_cli_channel_inline_argv(self, registry):
        from moa_gateway.capability.channels import CLIChannel

        ch = CLIChannel(argv=[PY, "-c", "print('inline-channel')"], registry=registry)
        res = await ch.execute("ignored")
        assert res.success is True
        assert res.output.strip() == "inline-channel"

    @pytest.mark.anyio
    async def test_api_channel_real_pool_call(self):
        from moa_gateway.capability.channels import APIChannel

        pool = FakePool(endpoints={"ep1": FakeEndpoint("ep1")}, content="real-api-output")
        ch = APIChannel(pool=pool)
        res = await ch.execute("hello api")
        assert res.success is True
        assert res.output == "real-api-output"
        assert pool.calls and pool.calls[0][1][-1]["content"] == "hello api"

    @pytest.mark.anyio
    async def test_api_channel_no_endpoint_classified_auth(self):
        from moa_gateway.capability.channels import APIChannel

        ch = APIChannel(pool=FakePool(endpoints={}))
        res = await ch.execute("x")
        assert res.success is False
        assert res.error.startswith("auth:"), res.error

    @pytest.mark.anyio
    async def test_api_channel_empty_response_classified_empty(self):
        from moa_gateway.capability.channels import APIChannel

        ch = APIChannel(pool=FakePool(endpoints={"ep1": FakeEndpoint("ep1")}, content="   "))
        res = await ch.execute("x")
        assert res.success is False
        assert res.error.startswith("empty:"), res.error

    @pytest.mark.anyio
    async def test_chain_real_cli_success(self, registry):
        from moa_gateway.capability.channels import ChannelChain, CLIChannel

        _register_py_echo(registry, "chain-tool")
        chain = ChannelChain([CLIChannel(tool="chain-tool", registry=registry)])
        out = await chain.execute("q", params={"payload": "chain-real"})
        assert out["channel"].value == "ch2"
        assert out["result"].success is True
        assert out["result"].output == "chain-real"
        assert [c.value for c in out["fallback_path"]] == ["ch2"]

    @pytest.mark.anyio
    async def test_chain_fallback_to_api(self, registry):
        """CH2 未配置 → 真实失败 → chain fallback 到 CH3 (fake pool 接管)。"""
        from moa_gateway.capability.channels import APIChannel, ChannelChain, CLIChannel

        pool = FakePool(endpoints={"ep1": FakeEndpoint("ep1")}, content="api-saved-it")
        chain = ChannelChain([CLIChannel(registry=registry), APIChannel(pool=pool)])
        out = await chain.execute("q")
        assert out["channel"].value == "ch3"
        assert out["result"].output == "api-saved-it"
        # 两路尝试:ch2 失败证据 + ch3 成功
        assert len(out["attempts"]) == 2
        assert out["attempts"][0].success is False
        assert out["attempts"][0].error.startswith("cli:")

    @pytest.mark.anyio
    async def test_chain_all_fail_raises_with_evidence(self, registry):
        from moa_gateway.capability.channels import APIChannel, ChannelChain, ChannelError, CLIChannel

        chain = ChannelChain(
            [CLIChannel(registry=registry), APIChannel(pool=FakePool(endpoints={}))]
        )
        with pytest.raises(ChannelError) as ei:
            await chain.execute("q")
        d = ei.value.to_dict()
        assert len(d["attempts"]) == 2
        assert d["attempts"][0]["error"].startswith("cli:")
        assert d["attempts"][1]["error"].startswith("auth:")

    @pytest.mark.anyio
    async def test_api_channel_real_model_pool(self, storage_instance):
        """真 ModelPool + MockProvider 端点 (D6 显式 mock 既定策略) — 走完整
        model_pool.call 链路 (端点选择/熔断/计费记录),而不是绕过。"""
        from moa_gateway.capability.channels import APIChannel
        from moa_gateway.config import ModelEndpointConfig, Settings
        from moa_gateway.model_pool import ModelPool

        settings = Settings(
            auth={
                "admin_username": "admin",
                "admin_password": "CliTestP@ss123!",
                "jwt_secret": "cli-test-secret-long-enough-for-hs256-xyz",
                "gateway_api_keys": [],
            },
            models=[
                ModelEndpointConfig(
                    id="ep-mock", provider="mock", model="mock-chat",
                    tier="standard", enabled=True,
                )
            ],
        )
        pool = ModelPool(settings=settings, storage=storage_instance)
        ch = APIChannel(pool=pool, endpoint_id="ep-mock")
        res = await ch.execute("say something")
        assert res.success is True, res.error
        assert res.output.strip()


# ============ 7. SubagentChannel 回环 (真实 ASGI app) ============


def _make_app_settings():
    from moa_gateway.config import ModelEndpointConfig, Settings

    return Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "CliTestP@ss123!",
            "jwt_secret": "cli-test-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["cli-test-api-key"],
        },
        models=[
            ModelEndpointConfig(
                id="ep-mock", provider="mock", model="mock-chat",
                tier="standard", enabled=True,
            )
        ],
    )


@pytest.fixture
async def gateway_app():
    test_settings = _make_app_settings()
    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            yield create_app()


class TestSubagentLoopback:
    @pytest.mark.anyio
    async def test_subagent_real_loopback(self, gateway_app):
        """真实经 ASGI 回环 /v1/chat/completions (内部鉴权头),非 mock 字符串。"""
        import httpx

        from moa_gateway.capability.channels import SubagentChannel

        transport = httpx.ASGITransport(app=gateway_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            ch = SubagentChannel(client=client, base_url="http://gw")
            res = await ch.execute("hello subagent")
        assert res.success is True, res.error
        assert res.output.strip()
        assert res.latency_ms >= 0

    @pytest.mark.anyio
    async def test_subagent_persona_as_system_prompt(self, gateway_app):
        import httpx

        from moa_gateway.capability.channels import SubagentChannel

        transport = httpx.ASGITransport(app=gateway_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            ch = SubagentChannel(client=client, base_url="http://gw")
            res = await ch.execute("hi", persona="You are a terse reviewer.")
        assert res.success is True, res.error

    @pytest.mark.anyio
    async def test_subagent_unreachable_classified_cli(self):
        """网关不可达 → 真实连接错误 → 归类 cli (兜底),chain 可继续 fallback。"""
        from moa_gateway.capability.channels import SubagentChannel

        ch = SubagentChannel(base_url="http://127.0.0.1:1", timeout_s=2)
        res = await ch.execute("x")
        assert res.success is False
        assert res.error.split(":")[0] in ("cli", "timeout")


# ============ 8. HTTP 端点 + RBAC ============

API_KEY = "cli-test-api-key"


@pytest.fixture
async def app_with_registry(gateway_app, storage_instance, monkeypatch):
    """把路由用的 registry 单例绑到隔离 storage 上。"""
    import moa_gateway.capability.cli_registry as cr

    monkeypatch.setattr(cr, "_registry", CLIRegistry(storage=storage_instance))
    return gateway_app


@pytest.fixture
async def client(app_with_registry):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app_with_registry)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def admin_jwt(app_with_registry):
    from moa_gateway.auth import create_jwt_token

    return create_jwt_token("admin", role="admin")


class TestHTTPEndpoints:
    @pytest.mark.anyio
    async def test_register_requires_admin(self, client):
        resp = await client.post(
            "/v1/capability/cli/tools",
            json={"name": "t1", "argv_template": [PY, "--version"]},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert resp.status_code == 401, "API key must NOT be able to register CLI tools"

    @pytest.mark.anyio
    async def test_register_with_admin_jwt(self, client, admin_jwt):
        resp = await client.post(
            "/v1/capability/cli/tools",
            json={"name": "pyver", "argv_template": [PY, "--version"]},
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["registered"]["name"] == "pyver"

    @pytest.mark.anyio
    async def test_register_blocked_executable_400(self, client, admin_jwt):
        resp = await client.post(
            "/v1/capability/cli/tools",
            json={"name": "evil", "argv_template": ["rm", "-rf", "/"]},
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert resp.status_code == 400
        assert "allowed_executables" in resp.text

    @pytest.mark.anyio
    async def test_list_readable_by_api_key(self, client, admin_jwt):
        await client.post(
            "/v1/capability/cli/tools",
            json={"name": "pyver", "argv_template": [PY, "--version"]},
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        resp = await client.get(
            "/v1/capability/cli/tools", headers={"Authorization": f"Bearer {API_KEY}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["tools"][0]["name"] == "pyver"

    @pytest.mark.anyio
    async def test_list_requires_some_credential(self, client):
        resp = await client.get("/v1/capability/cli/tools")
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_get_single_and_404(self, client, admin_jwt):
        await client.post(
            "/v1/capability/cli/tools",
            json={"name": "pyver", "argv_template": [PY, "--version"]},
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        ok = await client.get(
            "/v1/capability/cli/tools",
            params={"name": "pyver"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert ok.status_code == 200 and ok.json()["tool"]["name"] == "pyver"
        miss = await client.get(
            "/v1/capability/cli/tools",
            params={"name": "ghost"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert miss.status_code == 404

    @pytest.mark.anyio
    async def test_update_and_delete(self, client, admin_jwt):
        h = {"Authorization": f"Bearer {admin_jwt}"}
        await client.post(
            "/v1/capability/cli/tools",
            json={"name": "pyver", "argv_template": [PY, "--version"]},
            headers=h,
        )
        upd = await client.put(
            "/v1/capability/cli/tools/pyver",
            json={"description": "updated"},
            headers=h,
        )
        assert upd.status_code == 200
        assert upd.json()["updated"]["description"] == "updated"
        dele = await client.delete("/v1/capability/cli/tools/pyver", headers=h)
        assert dele.status_code == 200 and dele.json()["deleted"] is True
        again = await client.delete("/v1/capability/cli/tools/pyver", headers=h)
        assert again.status_code == 404

    @pytest.mark.anyio
    async def test_execute_requires_admin(self, client):
        resp = await client.post(
            "/v1/capability/cli/execute",
            json={"name": "pyver"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert resp.status_code == 401, "execute is an RCE primitive — admin only"

    @pytest.mark.anyio
    async def test_execute_real_python_version(self, client, admin_jwt):
        """E2E: 注册 → execute 真实返回 Python 版本号。"""
        h = {"Authorization": f"Bearer {admin_jwt}"}
        reg = await client.post(
            "/v1/capability/cli/tools",
            json={"name": "pyver", "argv_template": [PY, "--version"]},
            headers=h,
        )
        assert reg.status_code == 200, reg.text
        resp = await client.post(
            "/v1/capability/cli/execute", json={"name": "pyver"}, headers=h
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["exit_code"] == 0
        text = (data["stdout"] + data["stderr"]).strip()
        assert text.startswith("Python 3"), text
        assert data["latency_ms"] >= 0

    @pytest.mark.anyio
    async def test_execute_unknown_tool_400(self, client, admin_jwt):
        resp = await client.post(
            "/v1/capability/cli/execute",
            json={"name": "ghost"},
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_execute_batch_two_way_concurrent(self, client, admin_jwt):
        """E2E: batch 两路并发聚合,逐路真实证据。"""
        h = {"Authorization": f"Bearer {admin_jwt}"}
        for n, code in (("ba", "print('res-A')"), ("bb", "print('res-B')")):
            r = await client.post(
                "/v1/capability/cli/tools",
                json={"name": n, "argv_template": [PY, "-c", code]},
                headers=h,
            )
            assert r.status_code == 200, r.text
        resp = await client.post(
            "/v1/capability/cli/execute-batch",
            json={"items": [{"name": "ba"}, {"name": "bb"}]},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        assert data["ok_count"] == 2
        assert data["failed_count"] == 0
        outs = sorted(r["stdout"].strip() for r in data["results"])
        assert outs == ["res-A", "res-B"]

    @pytest.mark.anyio
    async def test_execute_batch_partial_failure(self, client, admin_jwt):
        h = {"Authorization": f"Bearer {admin_jwt}"}
        await client.post(
            "/v1/capability/cli/tools",
            json={"name": "ok-tool", "argv_template": [PY, "-c", "print('fine')"]},
            headers=h,
        )
        resp = await client.post(
            "/v1/capability/cli/execute-batch",
            json={"items": [{"name": "ok-tool"}, {"name": "missing-tool"}]},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok_count"] == 1 and data["failed_count"] == 1
        by_name = {r["name"]: r for r in data["results"]}
        assert by_name["ok-tool"]["ok"] is True
        assert by_name["missing-tool"]["ok"] is False

    @pytest.mark.anyio
    async def test_channels_endpoint_real_cli_no_mock_label(self, client, admin_jwt):
        """/v1/capability/channels 走真实 CH2:无 mock:True 标注,输出是子进程 stdout。"""
        h = {"Authorization": f"Bearer {admin_jwt}"}
        await client.post(
            "/v1/capability/cli/tools",
            json={
                "name": "ch-echo",
                "argv_template": [PY, "-c", "import sys; sys.stdout.write(sys.argv[1])", "{query}"],
            },
            headers=h,
        )
        resp = await client.post(
            "/v1/capability/channels",
            json={
                "action": "execute",
                "query": "real-channel-output",
                "enabled": ["ch2"],
                "cli_tool": "ch-echo",
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["channel"] == "ch2"
        assert data["success"] is True
        assert data["output"] == "real-channel-output"
        assert "mock" not in data, "real chain must not carry the mock label"
        assert resp.headers.get("X-MOA-Mock") != "true"

    @pytest.mark.anyio
    async def test_channels_endpoint_chain_info_and_classify(self, client):
        h = {"Authorization": f"Bearer {API_KEY}"}
        info = await client.post(
            "/v1/capability/channels", json={"action": "chain_info"}, headers=h
        )
        assert info.status_code == 200
        assert info.json()["order"] == ["ch1", "ch2", "ch3"]
        cls = await client.post(
            "/v1/capability/channels",
            json={"action": "classify_error", "error": "401 unauthorized"},
            headers=h,
        )
        assert cls.status_code == 200
        assert cls.json()["classification"] == "auth"
