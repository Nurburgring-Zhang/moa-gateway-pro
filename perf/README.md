# perf/ — MoA Gateway Pro 性能 + 故障注入测试

## 跑测试

```powershell
# 启动 server (需要 mock API key 否则 chat 503)
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "TestPass#2024"
$env:DEEPSEEK_API_KEY = "sk-mock"
$env:OPENAI_API_KEY = "sk-mock"
$env:ANTHROPIC_API_KEY = "sk-mock"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 127.0.0.1 --port 8088

# 跑性能压测
.venv\Scripts\python perf/bench.py

# 跑故障注入
.venv\Scripts\python perf/chaos.py
```

## bench.py 输出 (Windows, 1 uvicorn worker)

| 场景 | RPS | p50 | p95 | p99 | errs |
|---|---|---|---|---|---|
| /health 顺序 1000 | 1477 | 1ms | 1.2ms | 1.5ms | 0/1000 |
| /health 异步 200×20 | 686 | 19ms | 67ms | 86ms | 0/200 |
| /health 异步 500×30 | 446 | 45ms | 174ms | 264ms | 0/500 |
| /v1/chat 异步 100×10 (mock) | 25 | 363ms | 579ms | 611ms | 0/100 |

## 限制

- **Windows ephemeral port 1000 上限** — 高并发 (≥100 threads) 会撞 WinError 10048
  - 商用 Linux 默认无此限制
- **1 uvicorn worker** — 测的是单进程;生产用 `--workers 4` 翻 3-4 倍
- **mock provider** — LLM 调用走本地 mock,真实 provider 取决于外部 API 响应时间

## chaos.py 输出 (2026-07-19)

```
[1] 异常输入         7/7 ✓
  1.1 1.5MB body > 1MB → 413 ✓ (middleware 拦截)
  1.2 SQL 注入 → 200 (不执行) ✓
  1.3 XSS → 200 (不执行) ✓
  1.4-1.6 Pydantic 422 ✓
  1.7 未知 model → 503 ✓

[2] 鉴权            5/5 ✓
  2.1 无 key → 401 ✓
  2.2 错 key → 401 ✓
  2.3 错 scheme → 401 ✓
  2.4 100KB token → 401 (无内存炸弹) ✓
  2.5 admin endpoint 需 admin → 401 ✓

[3] 速率限制         1/1 ✓
  3.1 5 RPM key 打 6 → 第 6 个 429 ✓

[4] 公共端点        6/6 ✓
  4.1 /v1/models → 200 ✓
  4.2 /health → 200 ✓
  4.3 /docs Swagger UI → 200 ✓
  4.4 /openapi.json → 200 ✓
  4.5 /v1/nonexistent → 404 ✓
  4.6 DELETE /v1/chat → 405 ✓

总结: 19 pass, 0 fail
```
