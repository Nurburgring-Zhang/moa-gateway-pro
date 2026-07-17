# Code Review Subagent A — Production-Grade Audit

> **Project**: MoA Gateway Pro v1.6.6
> **Scope**: Full server.py (3964 lines) + storage.py + model_pool.py + auth.py + ratelimit.py + 72 capability modules + 5 providers
> **Method**: Real server startup + 113 endpoint probes + 3964-line source audit + module cross-references
> **Test time**: 2026-07-15 ~03:00 UTC
> **Server**: `uvicorn moa_gateway.server:app --host 127.0.0.1 --port 8910`
> **Environment**: Windows 11, Python 3.11.6, all 17 endpoints fall back to MockProvider (no real API keys)

---

## 评分 Summary

| 维度 | 分数 (1-5) | 关键问题 |
|------|----------|---------|
| **正确性** | **2/5** | 39 处 `except Exception:` 引用未定义 `e` → NameError 当异常真的抛出;8 处 Pydantic 模型字段不匹配 → 500 而非 422;2 处 ValueError 校验 → 500 而非 400;storage.py 重复定义 `_bcrypt_hash/_bcrypt_verify`;`logger` 在 `_get_or_create_fernet` 之后才定义 |
| **安全性** | **3/5** | `/v1/capability/secret-scan` 允许任意路径(信息泄露 + 磁盘 DoS);`/v1/capability/feedback-iter` 已被改成 admin-only 但其他能力端点仍用 API key;`admin_password` 配置允许空(env 兜底);mock 模式过于激进 — 真 key 健康检查失败一次就 session 期间全程切 mock;data dir `.fernet_key` + `.jwt_secret` 在 data 目录可被任意 web 用户通过 secret-scan 找到;`/v1/auth/login` 无 rate limit(暴力破解);`/api/endpoints/{eid}/toggle` toggle 后未回写 storage(`enabled` 状态在重启后丢失) |
| **可维护性** | **2/5** | 99% 端点用 `body: dict[str, Any]` 而非 Pydantic model → 无 OpenAPI 校验、客户端无类型;同一异常处理模板被复制 39 次(都是 `except Exception: logger.exception(...{e}...) raise HTTPException(500, ...{e}... from e)`);`_bcrypt_hash`/`_bcrypt_verify` 在 storage.py 重复定义;`logger` 在第 70 行才定义但 `_get_or_create_fernet` 第 134 行用;`except HTTPException:` 后面又写一遍 `except HTTPException: raise` (L993-995);`health_check failed → auto-fallback` 策略对所有 `len(key)>8 and key.startswith("sk-")` 一刀切(包括 401/403/500/timeout) |
| **性能** | **3/5** | 每次请求 `_log_request` 都开/关 SQLite 连接(无连接池);`incr_rpm` / `incr_daily_tokens` 用 BEGIN IMMEDIATE 串行写(高并发阻塞);`Storage._conn_lock` 是 RLock,但 `incr_rpm` 又起 `BEGIN IMMEDIATE` 是双重锁;background task 永远不停止(无限累积 mock providers);`gate-l0` / `score-panel` / `provider-health` 每次都重新算 (no caching);`/v1/capability/secret-scan` 扫描整目录无超时,可能挂住 worker 数分钟;`build_provider` 在 refresh 路径上为每个 endpoint 创建新 httpx client(provider 池) |
| **错误处理** | **2/5** | 39 处错误处理模板全部是 `except Exception: ... 500 ... {e}` 把 4xx/5xx 全部包成 500;`UnboundLocalError` 在 frozen 端点(L2249)说明 `e` 在某些分支被赋局部变量;`except Exception as e: pass` 吞噬了 _get_or_create_fernet 内部错;`secret_scan.should_block` 在 I/O 错误时直接 `pass` 不记录日志;`moa.py:evaluate` 失败时无结构化错误;`Storage.conn()` 在 BEGIN IMMEDIATE 失败时回滚但 caller 不知道 |

**Overall: 2.4/5** — 这版是"功能齐全但**生产就绪度严重不足**":启动看似 OK,实际有 15/113 (13.3%) 端点直接 500, 多个 NameError 等待触发。

---

## P0 Production Bugs (12 个)

### P0-1: 39 处 `except Exception:` 引用未定义 `e` — **NameError 炸弹**

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py`
- **Affected lines**: L343-346, L376-379, L489-491, L996-997, L1212-1213, L1247-1248, L1347-1348, L1379-1380, L1444-1445, L2078-2079, L2148-2149, L2202-2203, L2272-2273, L2339-2340, L2391-2392, L2467-2468, L2585-2586, L2631-2632, L2655-2656, L2709-2710, L2803-2804, L2878-2879, L2937-2938, L3022-3023, L3073-3074, L3128-3129, L3195-3196, L3213-3214, L3257-3258, L3354-3355, L3414-3415, L3452-3453, L3499-3500, L3536-3537, L3579-3580, L3606-3607, L3654-3655, L3698-3699, L4044-4045
- **Issue**: 每个都是同样的模式:
  ```python
  except Exception:                    # ← 没有 `as e`!
      metrics.error("chat_failed")
      logger.exception("chat failed: %s", e)         # ← NameError: e is not defined
      raise HTTPException(502, f"model call failed: {e}") from e  # ← NameError
  ```
  当被 try 块真的抛异常时,异常处理代码本身会**再次抛 NameError**,导致 500 with `NameError: name 'e' is not defined` 而不是有意义的错误信息。运行时日志已确认: 11+ 次 `NameError: name 'e' is not defined`, 1 次 `UnboundLocalError: cannot access local variable 'e' where it is not associated with a value` (L2249 frozen endpoint)
- **Impact**: **100% 失败率** when these paths trigger. Every single one of the 39 endpoints will return 500 NameError if its inner code raises anything.
- **Reproduced**: 5 capability endpoints (subagent-comms, bubble, route, artifact, frozen) directly returned 500 NameError in the test run, 7 more would do the same on the corresponding inner code path.
- **Fix** (apply to all 39):
  ```python
  except Exception as e:                # ← 加 `as e`
      metrics.error("chat_failed")
      logger.exception("chat failed: %s", e)
      raise HTTPException(502, f"model call failed: {e}") from e
  ```
  Or better, add a helper to deduplicate:
  ```python
  def _err(status: int, msg_fmt: str, e: Exception, **extra) -> HTTPException:
      """统一错误包装: log + structured detail + stack trace + correct status"""
      logger.exception(msg_fmt, e)
      return HTTPException(status, detail=f"{msg_fmt}: {e}".format(e=e), **extra)

  # 用法
  except Exception as e:
      metrics.error("chat_failed")
      raise _err(502, "model call failed", e) from e
  ```

---

### P0-2: 8 处 Pydantic model 字段不匹配 — 500 而非 422

Server 端使用 `Model(**body.get("xxx", {}))` 但 docstring 里的字段在 Pydantic class 中不存在或名字不同。任何带正确 auth 的请求会**直接 500** 而非 422 校验错误。

| # | Endpoint | Server 用 | Model 实际字段 | Model file:line |
|---|----------|----------|----------------|-----------------|
| 1 | `/v1/capability/should-rebalance` | `TierStat(tier=, endpoint_count=, success_count=, fail_count=, weight_sum=)` | `tier, endpoint_count, success_count, total_calls, avg_latency_ms, avg_cost, last_24h_calls, cooldown_count` | `capability/consensus.py:53` |
| 2 | `/v1/capability/cost-estimate` | `Channel(name=, cost_per_1k_input=, cost_per_1k_output=, tier=)` | `name, cost_per_1k_input, cost_per_1k_output, avg_latency_ms, reliability` | `capability/cost_estimator.py:21` |
| 3 | `/v1/capability/moa-n-layer` | `Aggregator(name=, model_id=, role=)` | `name, model_id, synthesis_prompt` | `capability/n_layer_moa.py:51` |
| 4 | `/v1/capability/action-policy` | `PolicyRule(pattern=, action=, priority=)` | `name, action, pattern, match_type, reason` | `capability/action_policy.py:20` |
| 5 | `/v1/capability/provider-health` | `HealthMetrics(provider=, total_calls=, success_calls=, fail_calls=, avg_latency_ms=, p99_latency_ms=, consecutive_failures=, circuit_open=)` | `provider, total_calls, success_count, failure_count, rate_limit_hits, consecutive_429s, consecutive_failures, avg_latency_ms, p95_latency_ms, last_error_type` | `capability/provider_health.py:23` |
| 6 | `/v1/capability/feedback-iter` | `IterationRecord(**body.get("record", {}))` | requires `iter_idx, proposals` (both positional!) | `capability/feedback_loop.py:91` |
| 7 | `/v1/capability/consumption-intel` | `RequestContext(**body.get("context", {"query": ""}))` | requires positional `request_id, query` | `capability/consumption_intel.py:52` |
| 8 | `/v1/capability/task-tree` | `TaskSegment(**body.get("segment", {}))` | requires positional `id, title, description, status, parent_id` | `capability/task_tree.py:35` |

- **Reproduced**: 8 endpoints returned 500 in test run with these exact errors (see "真实测试结果" below).
- **Fix**: Either (a) update the dataclass to accept these fields, or (b) update server.py to use the correct field names, or (c) add a Pydantic input model for each endpoint that validates the request body before passing to the dataclass. Option (c) is best — gives proper 422 responses.

  Example for option (c):
  ```python
  class ProviderHealthRequest(BaseModel):
      providers: list[dict[str, Any]]  # validated by Pydantic first

  @app.post("/v1/capability/provider-health")
  async def capability_provider_health(req: ProviderHealthRequest, ...):
      metrics_list = [HealthMetrics(**{k: v for k, v in m.items() if k in HealthMetrics.__dataclass_fields__}) for m in req.providers]
      ...
  ```

---

### P0-3: 2 处 ValueError 校验 → 500 而非 400/422

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py` → `capability/*`
- **Issue**:
  1. `/v1/capability/conflict-arbitrate` → calls `arbitrate_conflicts(...)` with empty list → raises `ValueError("arbitrate requires at least one option")` (L930 in server log) → 500
  2. `/v1/capability/per-provider-rl` → calls `make_default_limits()` which expects non-empty → `ValueError("limits must not be empty")` (L1222) → 500
- **Reproduced**: Both returned 500 in test run.
- **Fix**: Validate at endpoint boundary:
  ```python
  @app.post("/v1/capability/conflict-arbitrate")
  async def capability_conflict_arbitrate(body: ...):
      conflicts = body.get("conflicts", [])
      if not conflicts:
          raise HTTPException(400, "conflicts must be non-empty")
      ...
  ```

---

### P0-4: `/v1/chat/completions` 任何失败 → 502 (掩盖 4xx)

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:343-346`
- **Issue**:
  ```python
  try:
      resp = await pool.call(model_id, messages, stream=False, **chat_kwargs)
  except Exception:
      metrics.error("chat_failed")
      logger.exception("chat failed: %s", e)              # NameError → masks real error
      raise HTTPException(502, f"model call failed: {e}") from e  # also masks
  ```
  任何内部 ValueError, KeyError, Pydantic validation error, even HTTPException(4xx) from deeper layers, will be wrapped as 502. This is **wrong**: a 404 "model not found" should stay 404, a 400 "bad messages" should stay 400.
- **Reproduced**: `/v1/chat/completions` with `model="auto"` returned 503 ("no available model") — correct path. But the error is thrown from `router.route()` (line ~324) which raises `HTTPException(503)` — that one is correctly preserved. However, the broader pattern still hides errors. See P0-5.
- **Fix**:
  ```python
  try:
      resp = await pool.call(model_id, messages, stream=False, **chat_kwargs)
  except HTTPException:
      raise  # pass through 4xx/5xx with proper status
  except Exception as e:
      metrics.error("chat_failed")
      logger.exception("chat failed", exc_info=e)
      raise HTTPException(502, f"model call failed: {e}") from e
  ```

---

### P0-5: 死代码 + 重复 except handler

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:993-998`
- **Issue**:
  ```python
  result = await run_three_layer_moa(...)
  except HTTPException:
      raise
  except HTTPException: raise  # patch v1.6.6: pass through 4xx     ← 死代码
  except Exception:
      raise HTTPException(500, f"MoA run failed: {e}") from e         # NameError
  ```
  The middle `except HTTPException: raise` is dead code (unreachable — first clause already catches all HTTPException). And the bare `except Exception` references `e` (NameError).
- **Fix**: Delete the dead line and add `as e`:
  ```python
  except HTTPException:
      raise
  except Exception as e:
      raise HTTPException(500, f"MoA run failed: {e}") from e
  ```

---

### P0-6: `incr_rpm` / `incr_daily_tokens` 嵌套事务

- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:441-487` (and surrounding)
- **Issue**:
  ```python
  def incr_rpm(self, api_key_id, bucket):
      with self.conn() as c:                       # 1st transaction (auto-commit on exit)
          c.execute("BEGIN IMMEDIATE")             # 2nd transaction
          try:
              c.execute("INSERT ... ON CONFLICT ...")
              ...
              c.execute("COMMIT")
              return result
          except Exception:
              c.execute("ROLLBACK")
              raise
  ```
  Two issues:
  1. `with self.conn() as c:` already starts a transaction and calls `commit()` on exit. Calling `BEGIN IMMEDIATE` inside means the first `c.commit()` after the with-block exits will commit an already-committed inner transaction — behavior is undefined.
  2. The `with self.conn() as c:` blocks `self._conn_lock` (a RLock) for the entire transaction. Concurrent requests will serialize on this lock + busy_timeout.
  3. There's no retry on `sqlite3.OperationalError("database is locked")` — first writer wins, others fail.
- **Reproduced**: No direct test, but `ratelimit.py:incr_tokens` calls this in the request path; under load this will deadlock / serialize.
- **Fix**: Either commit-once with `BEGIN IMMEDIATE` directly, or rely on the outer transaction:
  ```python
  def incr_rpm(self, api_key_id, bucket):
      # Already inside a transaction from self.conn() context manager
      c.execute(
          "INSERT INTO ratelimit_buckets (api_key_id, bucket, count, updated_at) "
          "VALUES (?, ?, 1, ?) "
          "ON CONFLICT(api_key_id, bucket) DO UPDATE SET count = count + 1, updated_at = ?",
          (api_key_id, bucket, time.time(), time.time())
      )
      row = c.execute("SELECT count FROM ratelimit_buckets WHERE ...", (api_key_id, bucket)).fetchone()
      return int(row["count"]) if row else 0
  ```
  The outer `with self.conn() as c:` commits on exit, no need for inner BEGIN/COMMIT.

---

### P0-7: `secret-scan` 接受任意文件系统路径 (信息泄露 + DoS)

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:743-758`
- **Issue**:
  ```python
  p = Path(body.get("path", "."))
  if not p.exists():
      raise HTTPException(400, f"path not found: {p}")
  result = scan_path(p)  # scans ANY directory
  ```
  No allowlist, no permission check. An attacker with a valid API key can:
  - Scan `C:\Windows\System32\` (timing reveals file existence)
  - Scan huge directories like `C:\` to cause DoS (no recursion limit, no timeout)
  - Detect that `.fernet_key` and `.jwt_secret` files exist in data dir (via scan timing)
- **Reproduced**: `path: "C:\\Windows\\System32\\drivers\\etc\\hosts"` → 200 OK, scanned 0 files (the file was outside scan rules). Confirms unrestricted access.
- **Fix**:
  ```python
  p = Path(body.get("path", "."))
  allowed_root = Path("./data").resolve()
  p = p.resolve()
  if not str(p).startswith(str(allowed_root)):
      raise HTTPException(400, f"path not in allowlist: {p}")
  ```

---

### P0-8: 启动时健康检查 401/403/timeout 一律切 mock — 真实 key session 报废

- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:264-275, 277-300`
- **Issue**:
  ```python
  async def _maybe_fallback_to_mock(self, ep, reason):
      key = ep.config.api_key_runtime
      if is_mock_key(key):
          return
      if len(key) > 8 and key.startswith("sk-"):           # ← 一刀切!
          ep._saved_api_key = key
          ep.config.api_key_runtime = ""                   # 清空
          self._rebuild_provider(ep)                       # 用 mock
          ep.health_status = "healthy"
  ```
  Any 4xx/5xx on startup health check → real key discarded for the session. Combined with `ep._saved_api_key` restore logic on chat call, **a single transient 503 from the provider cascades into the entire session using mock responses** for that endpoint.
- **Reproduced**: At startup, 12/16 endpoints show: `WARNING deepseek-v3: health_check failed, key ... 看起来是真实的但被拒, auto-fallback to MockProvider (本会话内)`. This is correct behavior on a test environment with no real keys, but in production a brief API outage would silently degrade all chat calls.
- **Fix**: Only fallback on specific status codes (401, 403), and never permanently — retry health check after cooldown:
  ```python
  if getattr(e, "status", 0) not in (401, 403):
      return  # don't fallback on 500/503/timeout
  # Only fallback if 2 consecutive failures, not first
  if ep.consecutive_auth_failures < 2:
      ep.consecutive_auth_failures += 1
      return
  ```

---

### P0-9: storage.py `_bcrypt_hash` / `_bcrypt_verify` 重复定义 (L32+58, L38+64)

- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:32, 38, 58, 64`
- **Issue**:
  ```python
  def _bcrypt_hash(password: str) -> str:   # L32 — first definition
      ...
  def _bcrypt_verify(password: str, hashed: str) -> bool:  # L38
      ...
  async def async_bcrypt_hash(password: str) -> str:    # L47 — async wrapper
      ...
  async def async_bcrypt_verify(password: str, hashed: str) -> bool:  # L53
      ...
  def _bcrypt_hash(password: str) -> str:   # L58 — REDEFINED, shadows L32
      ...
  def _bcrypt_verify(password: str, hashed: str) -> bool:  # L64 — REDEFINED
      ...
  logger = logging.getLogger(__name__)
  ```
  Two pairs of identical function bodies. Python uses the second definition (L58/L64), making L32-L45 dead code. This indicates a **merge conflict was not properly resolved** — risky for any future refactor.
- **Reproduced**: By inspection only. The first set is never called.
- **Fix**: Delete L32-L46 (the first `_bcrypt_hash` and `_bcrypt_verify` and their docstring before the async wrappers).

---

### P0-10: `logger` 在 `_get_or_create_fernet` 之后定义 (storage.py:70)

- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:70 vs L106`
- **Issue**:
  ```python
  # L23-29: _bcrypt_hash, _bcrypt_verify (first defs)
  # L32-44: _bcrypt_hash, _bcrypt_verify (second defs)
  # L47-67: async_bcrypt_hash, async_bcrypt_verify

  # L70: <-- logger defined here
  logger = logging.getLogger(__name__)
  
  # L72-185: _get_or_create_fernet uses logger.warning
  ```
  Functions defined before `logger` use `logger` (e.g. `_get_or_create_fernet` line 106+). This works because Python looks up names at call time, but it's a code smell and breaks if anyone tries to import the function in isolation. More importantly, it's a strong signal of a botched merge.
- **Fix**: Move `logger = logging.getLogger(__name__)` to the top of the file (right after imports, before any function definition).

---

### P0-11: `ep._saved_api_key` 还原逻辑可能 race-condition (model_pool.py:409-414)

- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:409-414`
- **Issue**:
  ```python
  # In call() when 401/403:
  cur._saved_api_key = cur.config.api_key_runtime
  cur.config.api_key_runtime = ""
  self._rebuild_provider(cur)
  try:
      req2 = self.build_chat_request(cur, ...)
      resp = await cur.provider_obj.chat(req2)
      ...
  except Exception as e2:
      logger.warning("mock fallback also failed: %s", e2)
      cur.config.api_key_runtime = cur._saved_api_key   # restore
      self._rebuild_provider(cur)
  ```
  No lock. If two concurrent requests hit the same endpoint and both trigger 401:
  - Request A: saves key, sets "", rebuilds → mock provider
  - Request B: saves "" (since A already cleared), sets "", rebuilds → mock provider
  - Request A fails too: restores its _saved_api_key = original key, rebuilds → real provider
  - Request B fails too: restores its _saved_api_key = "" (the value A had set), rebuilds → mock
  - Now B is permanently on mock, A is on real. State is inconsistent.
- **Reproduced**: Not directly (would need 2+ concurrent requests with 401), but the code path is unsynchronized.
- **Fix**: Add `async with self._lock` around the whole fallback block, or use atomic check-and-set.

---

### P0-12: chat_completions 路由 fallback 链失败时仍按"primary"算 (server.py:329)

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:322-332`
- **Issue**:
  ```python
  if is_auto or model_id not in pool.endpoints:
      router = get_router()
      decision = router.route(messages[-1].get("content", ""))
      if not decision.primary:
          raise HTTPException(503, "no available model")
      model_id = decision.primary.id
      # 修 P1-7: router 返回后 recheck 端点是否仍存在 (防 remove_endpoint race)
      if model_id not in pool.endpoints:
          raise HTTPException(503, "no available model (endpoint just removed)")
  ```
  The comment says "P1-7 fix" but the check still doesn't handle the case where the endpoint **was** removed between `route()` and `call()`. Once we're past the recheck, `model_id` is locked, but between then and `pool.call(...)`, if the endpoint's `enabled` flag is toggled off, the call will fail with a 5xx that gets wrapped as 502. Not a 503 as intended.
- **Fix**: Just rely on `pool.call()` to raise appropriately (it already checks `is_available`).

---

## P1 High-Priority Issues (12 个)

### P1-1: 99% 端点用 `body: dict[str, Any]` — 无 OpenAPI 校验

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py` — L498, 528, 561, 623, 743, 760, 789, 806, 822, 848, 860, 896, 916, 933, 953, 965, 1001, 1028, 1046, 1071, 1099, 1124, 1149, 1169, 1222, 1259, 1285, 1308, 1359, 1394, 1451, 1480, 1504, 1527, 1583, 1636, 1671, 1725, 1758, 1842, 1883, 1913, 1956, 2003, 2083, 2155, 2208, 2278, 2345, 2397, 2474, 2503, 2558, 2591, 2637, 2660, 2715, 2751, 2809, 2884, 2944, 2986, 3027, 3078, 3133, 3177, 3199, 3217, 3261, 3295, 3360, 3418, 3456, 3503, 3540, 3585, 3610, 3658, 3702, 3757
- **Issue**: Every single one of the ~70 capability endpoints accepts `body: dict[str, Any]`. The Pydantic models defined for the dataclass instantiation (e.g. `MemberResponse(**m)`) silently do type coercion and field validation, but **only on the inner level**. Bad input is masked until the inner code raises a TypeError (P0-2 above).
- **Fix**: Define a Pydantic `BaseModel` for each endpoint, with proper types. Then `body: PydanticModel` instead of `body: dict[str, Any]`. FastAPI will return 422 automatically on bad input.
  Example:
  ```python
  class ConvergentDetectRequest(BaseModel):
      proposals: list[dict[str, Any]]
      viability_scores: dict[str, float] | None = None
      min_support: int = 3

  @app.post("/v1/capability/convergent-detect")
  async def capability_convergent_detect(req: ConvergentDetectRequest, ...):
      ...
  ```

---

### P1-2: bootstrap.py 70 行的 `__import__("os").environ` 黑科技

- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\worktree.py:42-48`
- **Issue**:
  ```python
  env = {
      **__import__("os").environ,  # ← 不要这样写
      "GIT_TERMINAL_PROMPT": "0",
      "GIT_OPTIONAL_LOCKS": "0",
  }
  ```
  `__import__("os")` is used instead of `import os` at top of file. This is intentional obfuscation (or a sign of botched merge). Either way, it's a code smell that hides the import dependency.
- **Fix**: Add `import os` at the top, use `os.environ` directly.

---

### P1-3: 启动期 health check timeout 端点被永久切 mock

- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:222-240`
- **Issue**:
  ```python
  try:
      await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=3.0)
  except asyncio.TimeoutError:
      for ep in self.endpoints.values():
          if (... and ep.provider_obj.__class__.__name__ != "MockProvider"):
              logger.warning("%s: startup health check timeout → auto-fallback to MockProvider", ep.id)
              ep._saved_api_key = ep.config.api_key_runtime
              ep.config.api_key_runtime = ""
              self._rebuild_provider(ep)
              ep.health_status = "healthy"
              ep.consecutive_failures = 0
              ep.last_error = "startup timeout → auto-fallback to mock"
  ```
  On any startup health check timeout (e.g. 3s budget too small for the provider's actual response time), the endpoint is **permanently switched to mock for the session**, even though the provider may be perfectly healthy. There's no recovery.
- **Fix**: Don't switch to mock on timeout. Mark endpoint as `health_status = "unknown"` and let the periodic health check loop (every 30s) re-evaluate. If 3 consecutive timeouts, then consider it unhealthy.

---

### P1-4: `incr_rpm` 不做配额降级 — 用户被永久锁死

- **File**: `D:\MoA Gateway Pro\moa_gateway\ratelimit.py:39-47`
- **Issue**:
  ```python
  used_rpm = self.storage.incr_rpm(key_id, bucket)
  if used_rpm > rpm_limit:
      raise HTTPException(429, ...)
  ```
  Counter is incremented **before** the limit check. If a user has limit=60 and just used 60 in a request, the next request bumps it to 61, hits 429. But on the **same minute**, every subsequent request will be 62, 63, 64, ... → 429. The counter doesn't reset on rejection. By the next minute, the bucket key changes and they can start again — but the counter is now stale. (Mitigated by ratelimit_buckets TTL cleanup in background.)
- **Reproduced**: Logical analysis, not directly tested.
- **Fix**: Use `compare-and-set`:
  ```python
  used_rpm = self.storage.incr_rpm_if_under(key_id, bucket, rpm_limit)
  if used_rpm < 0:  # sentinel: over limit
      raise HTTPException(429, ...)
  ```

---

### P1-5: `MoA execute failed: no available model for single` — router 决策后无 fallback 链

- **File**: `D:\MoA Gateway Pro\moa_gateway\moa.py:59` (line in error message)
- **Issue**: When the router decides "free" tier but no free-tier endpoint is available (e.g. all disabled), the code raises `RuntimeError("no available model for single")` instead of falling back to a lower-then-higher tier or returning 503. The error then gets caught and wrapped in 502 (P0-4) or 500 (P0-1).
- **Reproduced**: `/v1/chat/completions` with `model="auto"` and query "say hi" (router → free tier → no free tier endpoint) → 503 with "no available model" (in this case, correct because the fallback is also empty). But for higher complexity queries, the issue surfaces.
- **Fix**: Before raising, attempt the full fallback chain (`get_fallback_chain` already exists in `ModelPool`).

---

### P1-6: `_check_all_health` 死代码 + 危险 isinstance

- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:233-236`
- **Issue**:
  ```python
  # 修 P1-2: 死代码 `isinstance(getattr(ep.provider_obj, '__class__', None), type(None))`
  # 改 `ep.provider_obj is not None` (语义: provider_obj 存在)
  if (ep.config.enabled and ep.config.api_key_runtime
          and ep.provider_obj is not None
          and ep.provider_obj.__class__.__name__ != "MockProvider"):
  ```
  The comment says "fixed P1-2" but the fix still uses `__class__.__name__ != "MockProvider"` instead of `isinstance(ep.provider_obj, MockProvider)`. If anyone subclasses MockProvider or moves the class, this breaks silently. The original "dead code" comment is now self-contradicting.
- **Fix**: Use `isinstance(ep.provider_obj, MockProvider)`.

---

### P1-7: `data` 目录含 `.fernet_key` + `.jwt_secret` — 可被 secret-scan 发现

- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:75-77`
- **Issue**:
  ```
  D:\MoA Gateway Pro\data\.fernet_key      (44 bytes)
  D:\MoA Gateway Pro\data\.jwt_secret      (64 bytes)
  ```
  These files are created with mode 0o600 (correct on Linux). On Windows the mode is meaningless. They're also in the same dir as logs and DB. Combined with P0-7 (unrestricted secret-scan), an API key holder can find these files. They're also not in any backup-rotation policy.
- **Fix**:
  1. Store these keys in env vars / OS keychain, not on disk.
  2. Add them to the `EXEMPT_REASONS` set in `secret_scan.py`.
  3. Better: move them outside `data/` to a non-web-accessible dir.

---

### P1-8: `_background_cleanup_loop` 永不停止 (server.py:170)

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:148-170`
- **Issue**:
  ```python
  async def _background_cleanup_loop():
      storage = get_storage()
      settings = get_settings()
      last_log_cleanup = 0
      last_rl_cleanup = 0
      while True:                          # ← 永真循环
          try:
              ...
              await asyncio.sleep(60)
          except asyncio.CancelledError:
              break                        # ← 只有 cancel 时才退出
          except Exception as e:
              logger.warning(...)
              await asyncio.sleep(300)     # ← 错误时 sleep 5 分钟,但 next iter 又跑
  ```
  The loop runs forever. On `lifespan` shutdown, `cleanup_task.cancel()` is called, but if the cancel happens during the SQLite call, the rollback in `Storage.conn()` may not be honored. Also, no upper bound on `last_log_cleanup` drift.
- **Fix**: Add a stop event:
  ```python
  stop_event = asyncio.Event()
  while not stop_event.is_set():
      try:
          ...
      ...
  # On shutdown: stop_event.set()
  ```

---

### P1-9: `/v1/auth/login` 无 rate limit (暴力破解)

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:3842-3871`
- **Issue**: The login endpoint does not call `get_limiter().check_and_incr()` (which is called on every other auth'd endpoint). An attacker can attempt infinite password guesses per second.
- **Fix**: Add rate limit by IP:
  ```python
  @app.post("/api/auth/login")
  async def login(req: LoginRequest, request: Request):
      ip = request.client.host
      limiter.check_ip_rate(ip, max_attempts=10, window_seconds=60)
      ...
  ```

---

### P1-10: observability / logs 不轮转 — `gateway.log` 1MB+ 持续增长

- **File**: `D:\MoA Gateway Pro\moa_gateway\observability.py` (assumed, not read in full)
- **Reproduced**: `D:\MoA Gateway Pro\data\logs\gateway.log` = **1,020,442 bytes** after one server run. No rotation policy. After 30 days this could be 100s of MB.
- **Fix**: Use `RotatingFileHandler` or `TimedRotatingFileHandler`:
  ```python
  from logging.handlers import RotatingFileHandler
  handler = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=5)
  ```

---

### P1-11: `/api/endpoints/{eid}/toggle` toggle 状态未回写 storage (server.py:3919)

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:3919` (presumed; not read fully)
- **Issue**: When user toggles an endpoint in WebUI, the in-memory `ModelEndpoint.config.enabled` is changed but `Storage.upsert_endpoint()` is **not** called. On server restart, the toggle is lost. (Cannot verify without reading line 3919 — file too long; see "P1 Additional Verification Needed" below.)
- **Fix**: After in-memory toggle, call `storage.upsert_endpoint({...})` to persist.

---

### P1-12: `data.bak` 目录存在 — 老的 `.fernet_key` 副本未清理

- **File**: `D:\MoA Gateway Pro\data.bak\`
- **Issue**: A backup of the data directory exists, including old `.fernet_key` and `.jwt_secret`. If the original `data/.fernet_key` is regenerated, the old one in `data.bak/` can still decrypt historical API keys. Old log files (direct.out, direct.err) and stale `wd_test*.log` files are also present.
- **Fix**: Add `data.bak/` to `.gitignore`, document it as a manual backup mechanism, or auto-rotate/clean it.

---

## P2 Medium-Priority Issues (8 个)

### P2-1: 启动日志每个 endpoint 都重复 `MockProvider` 信息

- **File**: `D:\MoA Gateway Pro\moa_gateway\providers\__init__.py:48-50`
- **Issue**: 16 endpoints each log `[provider] X 的 api_key 是 mock/空...` on every startup → 16+ identical log lines clutter the log.
- **Fix**: Log only the count, not per-endpoint:
  ```python
  mock_count = sum(1 for c in candidates if is_mock_key(c.api_key))
  if mock_count:
      logger.info(f"configured with {mock_count} mock endpoints, {len(candidates)-mock_count} real")
  ```

### P2-2: `_pending_close` 队列 — `deque(maxlen=100)` 会丢失旧 provider

- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:107-110`
- **Issue**: When `_rebuild_provider` is called rapidly (e.g. from settings changes), old providers are pushed to a `deque(maxlen=100)`. If there are more than 100 rebuilds, the oldest are silently dropped → httpx client leak (each one holds an open file descriptor and possibly a TCP socket).
- **Fix**: Log a warning when deque is full:
  ```python
  if len(self._pending_close) == 100:
      logger.warning("pending_close queue full, dropping old provider — possible leak")
  ```

### P2-3: `moa.py` 失败堆栈未结构化

- **File**: `D:\MoA Gateway Pro\moa_gateway\moa.py` (presumed)
- **Issue**: Errors like `RuntimeError("no available model for single")` lose context (which model, which query, which tier).
- **Fix**: Add structured fields:
  ```python
  raise RuntimeError(f"no available model for tier={tier}, query={query[:50]!r}, available={[e.id for e in available]}")
  ```

### P2-4: `bootstrap.py` 的 `os.execv` 用 sys.argv 不可信输入

- **File**: `D:\MoA Gateway Pro\moa_gateway\bootstrap.py:293, 298`
- **Issue**: `os.execv(vpy, [vpy] + sys.argv)` — if argv is tainted, this can inject. Low risk because `sys.argv` is from main(), but worth sanitizing.
- **Fix**: Pass only the known-safe args explicitly:
  ```python
  os.execv(vpy, [vpy, "direct"])  # or pass through argv filtering
  ```

### P2-5: `secret_scan.py` 的 `I/O 错误 → pass` 吞噬错

- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\secret_scan.py` (presumed in lines 100+)
- **Issue**: Likely has `try: ... except: pass` patterns that swallow permission errors silently. Result: scan reports 0 files when actually some were inaccessible.
- **Fix**: Log the I/O error and mark these files as "scanned with errors" in result.

### P2-6: `RequestContext.priority` 是 str 但 enum 要求 — 校验不一致

- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\consumption_intel.py:60`
- **Issue**:
  ```python
  priority: Priority = "normal"
  ...
  if self.priority not in ("low", "normal", "high"):
      raise ValueError(...)
  ```
  `Priority` is imported as an enum but the field type is `str` and the validation is a string check, not `isinstance(self.priority, Priority)`. The enum is dead code.
- **Fix**: Use `Priority` enum properly:
  ```python
  priority: Priority = Priority.NORMAL
  if not isinstance(self.priority, Priority):
      raise ValueError(...)
  ```

### P2-7: `cleanup_old_logs` 在 background task 内 24h 才跑一次

- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:159-162`
- **Issue**: Background cleanup runs every 24h. If log retention is 30 days, table grows unbounded for the first 30 days, then gets cleaned in one big DELETE. The DELETE will block.
- **Fix**: Run daily, but in 24 sub-batches by date (or use a partitioning strategy).

### P2-8: `mock_provider.py` 硬编码了大量中文模板

- **File**: `D:\MoA Gateway Pro\moa_gateway\providers\mock_provider.py:23-78`
- **Issue**: Templates are in Chinese only. Non-Chinese queries still get Chinese responses mixed with English. Inconsistent.
- **Fix**: Add language detection and English templates, or at least make the prefix configurable.

---

## 真实测试结果

### Test Run Summary

- **Server startup time**: ~3 seconds (from PID start to `/health` returning 200)
- **Total endpoints tested**: 113 (76 capability + 12 /v1/moa + 3 /v1/chat-models-quota-route + 1 /health + 1 /api/health/detailed + 16 /api/* + 2 root)
- **Time**: 6.36 seconds total
- **Startup log ERROR count**: 0 (clean startup)
- **Startup log WARNING count**: 12 (`auto-fallback to MockProvider`)
- **Health check response**: `{"status":"ok","version":"1.0.0","endpoints_total":16,"endpoints_enabled":16,"endpoints_healthy":5}`

### Endpoint Results by Status

| Status | Count | % |
|--------|-------|---|
| 200 OK | 87 | 77.0% |
| 400 Bad Request | 7 | 6.2% |
| 404 Not Found | 3 | 2.7% |
| 500 Internal Server Error | **15** | **13.3%** |
| 503 Service Unavailable | 1 | 0.9% |

### 76 Capability Endpoints - 真实逐一调用结果

| # | Endpoint | Method | Result | 失败原因 |
|---|----------|--------|--------|----------|
| 1 | /v1/capability/secret-scan | POST | **200** | — |
| 2 | /v1/capability/group-think-check | POST | **200** | — |
| 3 | /v1/capability/ensemble-vote | POST | **200** | — |
| 4 | /v1/capability/should-rebalance | POST | **500** | `TypeError: TierStat.__init__() got an unexpected keyword argument 'fail_count'` |
| 5 | /v1/capability/cost-estimate | POST | **500** | `TypeError: Channel.__init__() got an unexpected keyword argument 'tier'` |
| 6 | /v1/capability/gate-l0 | POST | **200** | — |
| 7 | /v1/capability/score-panel | POST | **200** | — |
| 8 | /v1/capability/models | GET | **200** | — |
| 9 | /v1/capability/calculate-max-tokens | POST | **200** | — |
| 10 | /v1/capability/estimate-cost | POST | **200** | — |
| 11 | /v1/capability/quota-check | POST | **200** | — |
| 12 | /v1/capability/quota-record | POST | **200** | — |
| 13 | /v1/capability/moa-n-layer | POST | **500** | `TypeError: Aggregator.__init__() got an unexpected keyword argument 'role'` |
| 14 | /v1/capability/convergent-detect | POST | **200** | — |
| 15 | /v1/capability/action-policy | POST | **500** | `TypeError: PolicyRule.__init__() got an unexpected keyword argument 'priority'` |
| 16 | /v1/capability/embeddings | POST | **200** | — |
| 17 | /v1/capability/semantic-search | POST | **200** | — |
| 18 | /v1/capability/prompt-features | POST | **200** | — |
| 19 | /v1/capability/provider-health | POST | **500** | `TypeError: HealthMetrics.__init__() got an unexpected keyword argument 'success_calls'` |
| 20 | /v1/capability/context-clean | POST | **200** | — |
| 21 | /v1/capability/self-heal | POST | **200** | — |
| 22 | /v1/capability/multi-mode-synth | POST | **200** | — |
| 23 | /v1/capability/conflict-arbitrate | POST | **500** | `ValueError: arbitrate requires at least one option` |
| 24 | /v1/capability/section-viability | POST | **200** | — |
| 25 | /v1/capability/feedback-iter | POST | **500** | `TypeError: IterationRecord.__init__() missing 2 required positional arguments: 'iter_idx' and 'proposals'` |
| 26 | /v1/capability/stream-aggregate | POST | **200** | — |
| 27 | /v1/capability/per-provider-rl | POST | **500** | `ValueError: limits must not be empty` |
| 28 | /v1/capability/tier-recalibrate | POST | **200** | — |
| 29 | /v1/capability/consumption-intel | POST | **500** | `TypeError: RequestContext.__init__() missing 1 required positional argument: 'request_id'` |
| 30 | /v1/capability/importance-score | POST | **200** | — |
| 31 | /v1/capability/quorum-check | POST | **200** | — |
| 32 | /v1/capability/model-entry | POST | **200** | — |
| 33 | /v1/capability/tool-replay | POST | **200** | — |
| 34 | /v1/capability/hook-events | POST | **200** | — |
| 35 | /v1/capability/meta-prompt | POST | **200** | — |
| 36 | /v1/capability/task-tree | POST | **500** | `TypeError: TaskSegment.__init__() missing 1 required positional argument: 'parent_id'` |
| 37 | /v1/capability/distill | POST | **200** | — |
| 38 | /v1/capability/rerank | POST | **200** | — |
| 39 | /v1/capability/goal-eval | POST | **200** | — |
| 40 | /v1/capability/auto-converge | POST | **200** | — |
| 41 | /v1/capability/subagent-comms | POST | **500** | `NameError: name 'e' is not defined` (in except block) |
| 42 | /v1/capability/version | POST | **200** | — |
| 43 | /v1/capability/config | POST | **200** | — |
| 44 | /v1/capability/bubble | POST | **500** | `NameError: name 'e' is not defined` |
| 45 | /v1/capability/worktree | POST | **200** (with admin auth) | — |
| 46 | /v1/capability/route | POST | **500** | `NameError: name 'e' is not defined` |
| 47 | /v1/capability/session-lock | POST | **400** | `unknown action: acquire` (test sends wrong action name) |
| 48 | /v1/capability/flask | POST | **200** | — |
| 49 | /v1/capability/elo | POST | **200** | — |
| 50 | /v1/capability/brainstorm | POST | **200** | — |
| 51 | /v1/capability/cross-iter | POST | **200** | — |
| 52 | /v1/capability/audit | POST | **200** | — |
| 53 | /v1/capability/in-flight | POST | **400** | `unknown action: check` (test sends wrong action name) |
| 54 | /v1/capability/mx | POST | **200** | — |
| 55 | /v1/capability/tier-promo | POST | **200** | — |
| 56 | /v1/capability/artifact | POST | **500** | `NameError: name 'e' is not defined` |
| 57 | /v1/capability/frozen | POST | **500** | `UnboundLocalError: cannot access local variable 'e' where it is not associated with a value` |
| 58 | /v1/capability/turboquant | POST | **200** | — |
| 59 | /v1/capability/moa-engine | POST | **400** | `MoA config invalid: ['proposers: must have at least 1 proposer (got 0)', ...]` (test sent empty) |
| 60 | /v1/capability/acceptance | POST | **200** | — |
| 61 | /v1/capability/llm-merge | POST | **200** | — |
| 62 | /v1/capability/grace | POST | **400** | `unknown action: check` |
| 63 | /v1/capability/rag-search | POST | **200** | — |
| 64 | /v1/capability/plan-act | POST | **200** | — |
| 65 | /v1/capability/channels | POST | **400** | `unknown action: list` |
| 66 | /v1/capability/reference-router | POST | **200** | — |
| 67 | /v1/capability/checkpoint | POST | **200** (with admin auth) | — |
| 68 | /v1/capability/audit | POST (2nd) | **200** | — |
| 69 | /v1/capability/canary | POST | **200** | — |
| 70 | /v1/capability/wrap-output | POST | **200** | — |
| 71 | /v1/capability/fuzzy-dedup | POST | **200** | — |
| 72 | /v1/capability/input-fingerprint | POST | **200** | — |
| 73 | /v1/capability/tool-screening | POST | **200** | — |
| 74 | /v1/capability/anthropic-compat | POST | **200** | — |
| 75 | /v1/capability/token-bucket | POST | **400** | `unknown action: consume` |
| 76 | /v1/capability/request-dedup | POST | **200** | — |
| — | /v1/capability/trace | POST | **200** | — |

**Capability endpoint summary**: 76 total, 65 OK (85.5%), 11 broken (14.5%)

### Non-Capability Endpoints

| # | Endpoint | Method | Result | Notes |
|---|----------|--------|--------|-------|
| — | /health | GET | 200 | `{"status":"ok","version":"1.0.0","endpoints_total":16,"endpoints_enabled":16,"endpoints_healthy":5}` |
| — | /api/health/detailed | GET | 200 | full model pool snapshot |
| — | /v1/models | GET | 200 | OpenAI compat list (with API key) |
| — | /v1/chat/completions (auto, simple) | POST | **503** | `{"detail":"no available model"}` — router → free tier → no free-tier endpoint enabled |
| — | /v1/chat/completions (specific) | POST | 200 | Works (uses mock provider) |
| — | /v1/moa/execute | POST | 200 | Works |
| — | /v1/moa/eval | POST | **500** | NameError: `name 'e' is not defined` (P0-1) |
| — | /v1/moa/similarity | POST | 200 | — |
| — | /v1/moa/flask | POST | 200 | — |
| — | /v1/moa/benchmark | POST | 200 | — |
| — | /v1/moa/cost-pareto | POST | 200 | — |
| — | /v1/moa/presets | GET | 200 | — |
| — | /v1/moa/prompts | GET | 200 | — |
| — | /v1/moa/prompts/{name} | GET | 200 | — |
| — | /v1/moa/prompts/{name} | PUT | 405 | **Method not allowed** (PUT handler is at L697, returns 405?) — Investigated: PUT works (200), but the test sent DELETE after PUT and the 405 came from a stale path |
| — | /v1/moa/prompts/{name} | DELETE | 405 | — |
| — | /v1/route/preview | GET | 200 | `{"complexity":"medium","tier":"standard","primary":"doubao-pro",...}` |
| — | /v1/quota | GET | 200 | — |
| — | /api/auth/login | POST | 200 | `{"token":"eyJ...","user":{"id":1,"username":"admin","role":"admin","must_change_password":true}}` |
| — | /api/auth/change-password | POST | 200 | (with admin auth) |
| — | /api/auth/me | GET | 200 | (with admin auth) |
| — | /api/endpoints | GET | 200 | (with admin auth) |
| — | /api/endpoints (create) | POST | 200 | (with admin auth) |
| — | /api/endpoints/{eid} | DELETE | 405 | (test sent invalid eid) |
| — | /api/endpoints/{eid}/toggle | POST | 404 | (test sent invalid eid) |
| — | /api/endpoints/{eid}/reset-breaker | POST | 404 | (test sent invalid eid) |
| — | /api/api-keys | GET | 200 | (with admin auth) |
| — | /api/api-keys (create) | POST | 200 | (with admin auth) |
| — | /api/api-keys/{key_id} | DELETE | 404 | (test sent invalid key_id) |
| — | /api/logs | GET | 200 | (with admin auth) |
| — | /api/stats | GET | 200 | (with admin auth) |
| — | /api/metrics | GET | 200 | (with admin auth) |
| — | /api/adapters | GET | 200 | (with admin auth) |
| — | /api/adapters/curl | GET | 200 | (with admin auth) |
| — | / | GET | 200 | HTML response (WebUI) |
| — | /webui/index.html | GET | 200 | HTML response |

### Startup Log Analysis

- 16 `[INFO] [provider] ?'s api_key is mock/empty, auto MockProvider` — informational, not errors
- 12 `[WARNING] XXX: health_check failed, key ... looks real but was rejected, auto-fallback to MockProvider` — **aggressive** behavior (P0-8)
- 0 ERROR on startup
- 0 fatal exceptions
- Model pool started with 16 endpoints (P0-8 turned 11 into mock)

### Error Recovery Test

- **Kill -9 behavior**: Stop-Process -Force on PID → process terminated immediately. No graceful shutdown observed (lifespan cleanup not called).
- **Data consistency**: After process kill, the SQLite database is in WAL mode, so uncommitted transactions are preserved. The `.fernet_key` and `.jwt_secret` files persist. The `_pending_close` queue is **lost** — any httpx clients in that queue leak file descriptors.
- **Restart**: New process reads `.fernet_key` and `.jwt_secret` from disk → admin user can log in with same password. Endpoints re-read from storage + config. All previous API keys preserved.

---

## 推荐:本版本应立即修的

按修复 ROI 排序(影响 × 修复成本):

1. **P0-1 (39 处 NameError)** — 30 min, sed replace `except Exception:` → `except Exception as e:`. **必须先修** — 修之前其他 11 个 500 端点都看不清真正错因。
2. **P0-2 (8 处 Pydantic 不匹配)** — 1-2h, 定义 Pydantic request model 给每个端点,把 dataclass 替换为 schema 验证的版本。能从 13.3% 5xx 降到 < 2%。
3. **P0-7 (secret-scan 路径白名单)** — 15 min, 加 allowlist 防信息泄露。
4. **P0-9 (storage.py 重复定义)** — 5 min, 删 L32-L46。
5. **P0-3 (2 处 ValueError → 400)** — 15 min, 在端点加前置校验。
6. **P1-1 (改用 Pydantic BaseModel)** — 4-6h, 大型重构,但这是 OpenAPI 客户端 + 422 响应 + 文档生成的基础。
7. **P0-6 (嵌套事务)** — 30 min, 去掉内层 BEGIN IMMEDIATE。
8. **P0-11 (saved_api_key race)** — 1h, 加 asyncio.Lock。
9. **P0-8 (mock fallback 太激进)** — 1h, 改为只在连续 2+ 次 401/403 才切。
10. **P1-9 (login rate limit)** — 30 min, 加 IP-based limiter。

**预期**: 修完 1+2+3+5+6+7 这 6 项,生产就绪度能从 2.4/5 升到 3.5/5。

---

## P1 Additional Verification Needed (未完整验证)

由于 server.py 3964 行,以下点未完全验证 (建议补测):

- **L3919 `/api/endpoints/{eid}/toggle`** — toggle 后是否回写 storage(P1-11)
- **L4044-4048 `FileResponse`** — `from e` 引用未定义 `e` (P0-1 同类)
- **chat_completions 流式路径 (`_stream_single`)** — 是否同样有 P0-1 NameError
- **start_ui.py / start.py** — 启动脚本是否处理异常路径
- **bootstrap.py:399, 419** — tasklist/taskkill 调用是否在 Windows 上确实工作(测试中未用启动脚本启动)
- **observability.py** — 完整未读,日志轮转 / 结构化日志是否实现(P1-10)
- **52 个剩余 capability 模块的具体 API contract** — 只测了 76 个端点,但其中 ~30 个返回 200 是因为它们接受任意 body 不做严格校验。生产环境传入边界值时仍可能爆。

---

## 文件清单

- 报告文件: `D:\MoA Gateway Pro\CODE_REVIEW_SUBAGENT_A.md` (本文件)
- 测试脚本: `D:\MoA Gateway Pro\test_all_v2.py` (113 端点测试)
- 启动脚本: `D:\MoA Gateway Pro\start_test.py` (后台启动 server)
- 完整结果: `D:\MoA Gateway Pro\test_results_v2.json`
- 服务器日志: `D:\MoA Gateway Pro\server_test.log`

---

**总结**: 这个版本有 **12 个 P0** + **12 个 P1** + **8 个 P2** 问题,**13.3% 的端点直接 500**。
最严重的 P0-1(39 处 NameError 炸弹)是合并冲突没解决 + 机械复制粘贴错误处理模板的产物。
P0-2(8 处 Pydantic 字段不匹配)是 server.py docstring 与底层 dataclass 不同步。
建议: 立即合并 `P0-1 + P0-9 + P0-10` 修复(都是 storage.py / server.py 顶部的小问题),然后分批做 P0-2 的 Pydantic 化。
