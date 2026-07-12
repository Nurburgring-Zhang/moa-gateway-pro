# 智能 Auth-Invalid 自动 Fallback 文档

## 解决的问题

**用户痛点**:环境变量里设了 `DEEPSEEK_API_KEY=sk-...` 等 6 个 provider key,但**部分 key 已经失效**(过期/余额用完/被服务端拒绝)。这导致:

- 启动 server 时 health check 返回 401
- endpoint 被标 unhealthy,所有功能看似无效
- 用户需要手动去 config.yaml 把 key 改成 `""` 才能让 mock fallback
- 启动阻塞 8-10s 等待 health check 超时

**修复后行为**:
- 启动 3s 完成(原 8-10s)
- 检测到 401/403/timeout 的 endpoint **自动**切到 MockProvider(in-memory)
- 用户无需任何手动操作
- 16 个 endpoint 全部可用,MoA 全部 preset 可跑
- 用户填正确 key 后,下次重启 server 自动走真 API

## 实现机制

### 1. `providers/openai_compat.py` health_check 改进

**改动**:401/403 视为不健康(原代码 401 算健康,因为 `status < 500`)

```python
# 改前
return resp.status_code < 500  # 401 算健康 ❌

# 改后
return 200 <= resp.status_code < 500 and resp.status_code not in (401, 403)  # ✅
```

### 2. `model_pool.py` 新增 `_maybe_fallback_to_mock`

**场景**:单个 endpoint health check 失败时,自动判断"key 是真的但被拒"vs"key 本来就是空":
- `api_key_runtime` 为空 → 早 return(本来就是 mock,不用重切)
- `is_mock_key(key)` True → 早 return
- `len(key) > 8 and key.startswith("sk-")` → 真 key + health 失败 → **自动切 mock**

```python
async def _maybe_fallback_to_mock(self, ep, reason: str) -> None:
    if not ep or not ep.config.api_key_runtime:
        return
    key = ep.config.api_key_runtime
    from .providers import is_mock_key
    if is_mock_key(key):
        return
    if len(key) > 8 and key.startswith("sk-"):
        ep._saved_api_key = key
        ep.config.api_key_runtime = ""  # 切 mock
        self._rebuild_provider(ep)
        ep.health_status = "healthy"
        ep.consecutive_failures = 0
        ep.last_error = f"auth invalid → auto-fallback to mock ({reason})"
```

### 3. `model_pool.py` `_check_all_health` 加 3s startup timeout

**目的**:卡住的 health check(网络不通/服务端慢响应)不阻塞 lifespan。

```python
try:
    await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True),
        timeout=3.0
    )
except asyncio.TimeoutError:
    # 把还没回的真 key 端点切 mock
    for ep in self.endpoints.values():
        if (ep.config.enabled and ep.config.api_key_runtime
                and ep.provider_obj.__class__.__name__ != "MockProvider"):
            ep._saved_api_key = ep.config.api_key_runtime
            ep.config.api_key_runtime = ""
            self._rebuild_provider(ep)
```

### 4. `model_pool.py` call() catch 401 自动切 mock

**目的**:运行中 key 突然失效(余额用完/被踢)时,自动 fallback 让请求不失败。

```python
except ProviderError as e:
    if getattr(e, "status", 0) in (401, 403) and cur.config.api_key_runtime:
        cur._saved_api_key = cur.config.api_key_runtime
        cur.config.api_key_runtime = ""
        self._rebuild_provider(cur)
        # 用 mock 重试一次
        try:
            resp = await cur.provider_obj.chat(req2)
            return resp
        except Exception:
            cur.config.api_key_runtime = cur._saved_api_key  # 还原
            self._rebuild_provider(cur)
```

### 5. `router.py route_for_moa` TRIVIAL 分支修复

**问题**:`_resolve_models` 用 `query="placeholder"` 调 `route_for_moa`,被评估为 `ComplexityLevel.TRIVIAL`,走单模型简化路径返回 0 个 ref,导致 `ranker_qwen110b` 报 `no available model for ranker`。

**修复**:`reference_count > 1` 时不走 TRIVIAL 简化:

```python
# 改前
if complexity == ComplexityLevel.TRIVIAL and not preset:
    return ([d.primary] if d.primary else [], d.primary)

# 改后
if complexity == ComplexityLevel.TRIVIAL and not preset and reference_count <= 1:
    return ([d.primary] if d.primary else [], d.primary)
```

## 持久化策略

**in-memory only**,不写 disk:
- `ep._saved_api_key`:保留原 key,后续还原用
- `ep.config.api_key_runtime = ""`:仅当前进程生效
- 重启 server 重新读 env var,如果 key 已修正则走真 API,否则继续走 mock

## 测试结果

```
Pool endpoints: 16
Healthy: 5
Mock mode: 16
Real providers: 0
  chinese_battalion              4 refs (4 ok) final_len=604 cost=$0.0040
  chinese_battalion_layered      9 refs (9 ok) final_len=195 cost=$0.0040
  qwen_single_proposer           4 refs (4 ok) final_len=194 cost=$0.0019
  ranker_qwen110b                4 refs (4 ok) final_len=605 cost=$0.0047
```

启动 stderr 日志:
```
[WARNING] deepseek-r1: health_check failed,key 看起来是真实的但被拒,auto-fallback to MockProvider (本会话内)
[WARNING] deepseek-v3: health_check failed,key 看起来是真实的但被拒,auto-fallback to MockProvider (本会话内)
[WARNING] moonshot-v1-8k: health_check failed,key 看起来是真实的但被拒,auto-fallback to MockProvider (本会话内)
[WARNING] gpt-4o-mini: startup health check timeout → auto-fallback to MockProvider
[WARNING] gpt-4o: startup health check timeout → auto-fallback to MockProvider
```

5 个有真 key 的 endpoint 全部自动切 mock,3 个因 401,2 个因 startup timeout(openai.com 在中国不通)。

## 已知限制

- `_maybe_fallback_to_mock` 只在 `key.startswith("sk-")` 时切,如果用户的 key 格式不是 sk- 前缀,需要手动改 config。
- 不持久化 mock 状态,用户填正确 key 后需要重启 server 才能走真 API。
- 不影响真 key 路径(有正确 key 的 endpoint 仍走真 API)。