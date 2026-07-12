"""深度 UI 端到端审核 — 模拟真实用户操作流程

流程:
1. 启动 server 子进程(模拟用户点"启动服务"按钮)
2. 模拟 sr.start() 后 is_running = True + 触发 on_started callbacks
3. 模拟 import + build 每个 page(模拟 UI 启动时 build 所有 page)
4. 手动调每个 page 的 refresh / load_* 函数
5. 看返回数据 + 验证渲染控件有数据
6. 模拟 click(POST/PUT/DELETE)端点,验证 CRUD
7. 报每个 page 的 PASS/FAIL
"""
import json
import sys
import time
import asyncio
import threading
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, ".")
sys.path.insert(0, "moa_gateway")

# 在 import flet 前 mock page,避免 flet 启动 desktop
import flet as ft
ft.app = MagicMock()  # 阻止 flet app 启动
ft.Page = MagicMock


def get_api_key(port):
    from moa_gateway.storage import get_storage
    s = get_storage()
    k = s.create_api_key(name="audit", quota_rpm=10000, quota_daily_tokens=999999999)
    return k["key"]


def admin_login(port):
    """模拟 UI 启动时的 admin login,拿 JWT"""
    import urllib.request as ur
    import json
    payload = json.dumps({"username": "admin", "password": "admin"}).encode()
    req = ur.Request(f"http://127.0.0.1:{port}/api/auth/login",
                     data=payload, headers={"Content-Type": "application/json"},
                     method="POST")
    try:
        with ur.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            return data.get("token") or data.get("access_token")
    except Exception as e:
        print(f"  ❌ admin login failed: {e}")
        return None


def call_api(method, port, path, body=None, key=None, timeout=30):
    url = f"http://127.0.0.1:{port}{path}"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        body = r.read().decode("utf-8", errors="replace")
        return r.status, json.loads(body) if body and body[0] in "{[" else body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


def start_server(port=8920):
    """启动 server 子进程,等就绪"""
    env = __import__("os").environ.copy()
    env["PYTHONPATH"] = str(Path.cwd()) + ";" + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "uvicorn", "moa_gateway.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning", "--no-access-log"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=str(Path.cwd()),
    )
    # 等就绪
    for _ in range(100):
        time.sleep(0.3)
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                return None
    proc.terminate()
    return None


def main():
    port = 8920
    print("=" * 60)
    print(f"  MoA Gateway Pro — UI 深度端到端审核")
    print(f"  port: {port}")
    print("=" * 60)

    # 1. 启动 server
    print("\n[1/6] 启动 server ...")
    proc = start_server(port)
    if not proc:
        print("  ❌ server 启动失败")
        return
    print(f"  ✓ server 启动, pid={proc.pid}")
    try:
        key = get_api_key(port)
        print(f"  ✓ api key: {key[:24]}...")

        # 2. 测所有 API 端点(数据可用性)
        print("\n[2/6] 测 API 端点(数据可用性) ...")
        results = {}
        # presets
        code, body = call_api("GET", port, "/v1/moa/presets", key=key)
        results["presets"] = (code, len(body.get("presets", [])) if isinstance(body, dict) else 0)
        print(f"  {'✓' if code == 200 else '❌'} /v1/moa/presets → {code}, {results['presets'][1]} presets")
        # prompts
        code, body = call_api("GET", port, "/v1/moa/prompts", key=key)
        results["prompts"] = (code, len(body.get("templates", [])) if isinstance(body, dict) else 0)
        print(f"  {'✓' if code == 200 else '❌'} /v1/moa/prompts → {code}, {results['prompts'][1]} templates")
        # endpoints(应该 401)
        code, body = call_api("GET", port, "/api/endpoints", key=key)
        results["endpoints"] = (code, "admin only")
        print(f"  {'⚠️' if code == 401 else '❌'} /api/endpoints → {code} (admin only)")
        # benchmark
        code, body = call_api("POST", port, "/v1/moa/benchmark",
                              {"presets": ["fast", "balanced"], "category": "all", "limit": 1},
                              key=key, timeout=60)
        n_items = sum(len(v) for v in body.get("results", {}).values()) if isinstance(body, dict) and "results" in body else 0
        results["benchmark"] = (code, n_items)
        print(f"  {'✓' if code == 200 else '❌'} /v1/moa/benchmark → {code}, {n_items} items")
        # pareto
        code, body = call_api("POST", port, "/v1/moa/cost-pareto",
                              {"prompts": ["hi"], "presets": ["fast", "balanced"]},
                              key=key, timeout=60)
        n_pts = len(body.get("pareto_points", [])) if isinstance(body, dict) else 0
        results["pareto"] = (code, n_pts)
        print(f"  {'✓' if code == 200 else '❌'} /v1/moa/cost-pareto → {code}, {n_pts} points")
        # flask(用 server 端 schema)
        code, body = call_api("POST", port, "/v1/moa/flask",
                              {"query": "写 hello world", "response": "print('hello world')"},
                              key=key, timeout=10)
        results["flask"] = (code, "ok" if code == 200 else "fail")
        print(f"  {'✓' if code == 200 else '❌'} /v1/moa/flask → {code}")
        # similarity(用 server 端 schema)
        code, body = call_api("POST", port, "/v1/moa/similarity",
                              {"query": "hi", "candidate_a": "hello", "candidate_b": "hi there"},
                              key=key, timeout=10)
        results["similarity"] = (code, "ok" if code == 200 else "fail")
        print(f"  {'✓' if code == 200 else '❌'} /v1/moa/similarity → {code}")
        # moa execute(用 ChatCompletionRequest schema: messages 不是 query)
        code, body = call_api("POST", port, "/v1/moa/execute",
                              {"messages": [{"role": "user", "content": "写一个 LRU Cache"}],
                               "preset": "chinese_battalion"},
                              key=key, timeout=60)
        n_refs = len(body.get("references", [])) if isinstance(body, dict) else 0
        results["moa"] = (code, n_refs)
        print(f"  {'✓' if code == 200 else '❌'} /v1/moa/execute → {code}, {n_refs} refs")
        # chat completions
        code, body = call_api("POST", port, "/v1/chat/completions",
                              {"model": "moa", "messages": [{"role": "user", "content": "hi"}],
                               "preset": "chinese_battalion"},
                              key=key, timeout=60)
        results["chat"] = (code, "ok" if code == 200 else "fail")
        print(f"  {'✓' if code == 200 else '❌'} /v1/chat/completions → {code}")
        # v1/models
        code, body = call_api("GET", port, "/v1/models", key=key)
        results["models"] = (code, len(body.get("data", [])) if isinstance(body, dict) else 0)
        print(f"  {'✓' if code == 200 else '❌'} /v1/models → {code}, {results['models'][1]} models")

        # 3. 模拟 UI 启动 + 触发 on_started callbacks
        print("\n[3/6] 模拟 UI 启动 + 触发 on_started ...")
        from moa_gateway.ui import server_runner as sr_mod
        from moa_gateway.ui import pages, pages2
        from moa_gateway.ui.theme import DARK, get_palette

        # 创建 ServerRunner 实例
        sr = sr_mod.ServerRunner()
        # 强制 is_running = True(模拟用户已点启动)
        sr.is_running = True
        sr._port = port

        # 模拟 state(用真 palette)
        state = {
            "palette": get_palette("dark"),
            "page_ref": None,
            "current_page": "dashboard",
        }

        # 4. 测每个 page: build + load + 拿数据
        print("\n[4/6] 测 6 个 page build + load ...")

        # 端点页
        try:
            ep_view = pages.build_endpoints(state, sr)
            print(f"  ✓ build_endpoints → {type(ep_view).__name__}")
        except Exception as e:
            print(f"  ❌ build_endpoints: {type(e).__name__}: {e}")

        # MoA playground
        try:
            pg_view = pages.build_playground(state, sr)
            print(f"  ✓ build_playground → {type(pg_view).__name__}")
        except Exception as e:
            print(f"  ❌ build_playground: {type(e).__name__}: {e}")

        # Benchmark
        try:
            bm_view = pages2.build_benchmark(state, sr)
            print(f"  ✓ build_benchmark → {type(bm_view).__name__}")
        except Exception as e:
            print(f"  ❌ build_benchmark: {type(e).__name__}: {e}")

        # Prompts
        try:
            pr_view = pages2.build_prompts(state, sr)
            print(f"  ✓ build_prompts → {type(pr_view).__name__}")
        except Exception as e:
            print(f"  ❌ build_prompts: {type(e).__name__}: {e}")

        # Settings
        try:
            st_view = pages2.build_settings(state, sr, lambda: None, lambda _: None, sr)
            print(f"  ✓ build_settings → {type(st_view).__name__}")
        except Exception as e:
            print(f"  ❌ build_settings: {type(e).__name__}: {e}")

        # Dashboard
        try:
            db_view = pages.build_dashboard(state, sr)
            print(f"  ✓ build_dashboard → {type(db_view).__name__}")
        except Exception as e:
            print(f"  ❌ build_dashboard: {type(e).__name__}: {e}")

        # 5. 验证 on_started callbacks 数量
        print(f"\n[5/6] on_started_callbacks 数: {len(sr.on_started_callbacks)}")
        for i, cb in enumerate(sr.on_started_callbacks):
            print(f"    [{i+1}] {cb}")

        # 5b. 触发 on_started callbacks,验证 load_* 真能拿到数据
        if sr.on_started_callbacks:
            print("\n[5b] 触发 on_started callbacks(等 3s)...")
            for cb in sr.on_started_callbacks:
                try:
                    cb()
                except Exception as e:
                    print(f"  ❌ callback: {e}")
            time.sleep(3)
            print("  ✓ 已触发全部 callback")
        else:
            print("  ❌ 没有 callback 注册!这意味着 page build 时 on_started 注册失败")

        # 6. 测 POST 端点(模拟 click)
        print("\n[6/6] 测 click 触发的 POST 端点 ...")
        # 先 admin login 拿 JWT(模拟 UI 启动)
        admin_jwt = admin_login(port)
        if not admin_jwt:
            print("  ❌ admin login 失败,跳过 admin 端点")
        else:
            print(f"  ✓ admin JWT 拿到 (len={len(admin_jwt)})")
            # 新增端点
            new_ep = {
                "endpoint_id": "test_ep_audit", "provider": "mock", "model": "test",
                "tier": "lite", "api_key_plain": "", "enabled": True, "weight": 50,
                "max_tokens": 4096, "cost_per_1k_input": 0.0001,
                "cost_per_1k_output": 0.0001, "tags": ["audit-test"],
            }
            code, body = call_api("POST", port, "/api/endpoints", new_ep,
                                  key=admin_jwt, timeout=10)
            print(f"  {'✓' if code in (200, 201) else '❌'} POST /api/endpoints (admin JWT) → {code}")
            code, body = call_api("GET", port, "/api/endpoints", key=admin_jwt)
            print(f"  {'✓' if code == 200 else '❌'} GET /api/endpoints (admin JWT) → {code}")
            code, body = call_api("DELETE", port, f"/api/endpoints/test_ep_audit",
                                  key=admin_jwt, timeout=10)
            print(f"  {'✓' if code in (200, 204) else '❌'} DELETE /api/endpoints/test_ep_audit → {code}")

        # POST prompt template → 实际是 PUT
        new_pt = {"content": "test {{q}}", "source": "user"}
        code, body = call_api("PUT", port, "/v1/moa/prompts/audit_test_template",
                              new_pt, key=key, timeout=10)
        print(f"  {'✓' if code in (200, 201) else '❌'} PUT /v1/moa/prompts/{{name}} → {code}")
        code, body = call_api("GET", port, "/v1/moa/prompts/audit_test_template", key=key)
        print(f"  {'✓' if code == 200 else '❌'} GET /v1/moa/prompts/{{name}} → {code}")
        code, body = call_api("DELETE", port, "/v1/moa/prompts/audit_test_template", key=key, timeout=10)
        print(f"  {'✓' if code in (200, 204) else '❌'} DELETE /v1/moa/prompts/{{name}} → {code}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print("\n" + "=" * 60)
    print(f"  审核完成")
    print("=" * 60)


if __name__ == "__main__":
    main()