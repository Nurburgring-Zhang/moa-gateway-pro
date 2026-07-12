# MoA Gateway Pro — 第 3 轮稳定性 / 性能 / 资源 审计报告

**审计范围**:并发安全、资源泄漏、超时控制、错误恢复、性能、可观测性
**不包含**:架构 / 安全 / 功能正确性
**审计基准**:第 1、2 轮已知问题之外,重点深挖运行时稳定性与故障行为
**审计文件**:`model_pool.py` / `storage.py` / `bootstrap.py` / `providers/*.py` / `server.py` / `moa.py` / `router.py` / `ratelimit.py` / `observability.py` / `auth.py` / `config.py`

---

## 风险等级图例
- **P0** — 必须立即修。线上必现或在故障链上,会拖垮进程或数据。
- **P1** — 应在下个迭代修。会显著放大故障面,或在高并发/异常路径下退化。
- **P2** — 改进项。不紧急,但长期会拖性能 / 维护成本。

---

# P0 — 紧急

---

## P0-1 · SQLite 没有启用 WAL / busy_timeout / 同步模式,默认 rollback journal 在并发写入下必锁表

**[storage.py:173]** `c = sqlite3.connect(str(self.db_path), timeout=30)`
- 整段代码没有调用任何 `PRAGMA journal_mode=WAL`、`PRAGMA synchronous=NORMAL`、`PRAGMA busy_timeout`、`PRAGMA temp_store=MEMORY`、`PRAGMA foreign_keys=ON`。
- 默认是 `journal_mode=DELETE`(rollback journal),整个数据库在写期间是**文件级排他锁**。
- `incr_rpm` / `incr_daily_tokens` / `log_request` / `_conn_lock` 全在 FastAPI 异步请求路径上被频繁触发,并发稍高(>10 RPS)就会出现 `OperationalError: database is locked`。
- `conn()` 每次都新建连接 + 立即 commit + close,连接池完全不存在,网络盘场景下文件系统 lock 也撑不住。

**影响**:
- 高并发请求下,管理员登录、写日志、记限流计数都会失败,报 500。
- 限流计数读后写(`incr_rpm` INSERT + SELECT)在并发下会丢失计数(见 P0-2)。
- 多 worker 部署(`server.workers>1`)时直接不可用:即使 fork 后连接关闭,DELETE journal 模式下跨进程写入必现锁等待。

**修复建议**:
```python
c = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA synchronous=NORMAL")
c.execute("PRAGMA busy_timeout=30000")
c.execute("PRAGMA temp_store=MEMORY")
c.execute("PRAGMA foreign_keys=ON")
```
并且把 `conn()` 改成"每次 acquire→borrow 一条长连接,关闭时归还"的连接池(可考虑 `aiosqlite` + 简单 asyncio.Queue 或 `SQLAlchemy async`)。当前实现是**每请求 1+ 次 connect/close**,性能也不达标。

---

## P0-2 · `incr_rpm` / `incr_daily_tokens` 的"读自增"是非原子的,会丢失计数和配额

**[storage.py:473-498]** 两个方法都是:
```python
c.execute("INSERT … ON CONFLICT … DO UPDATE SET count = count + 1, …")
row = c.execute("SELECT count FROM ratelimit_buckets WHERE …")
return int(row["count"]) if row else 0
```

- INSERT+SELECT 跨两个语句,中间窗口会被其他请求的 INSERT 插队。
- 即便加了 WAL,SQLite 默认事务隔离(`DEFERRED`)下两个并发事务可能都看到旧值。
- 由于是**先 INSERT 再 SELECT**(自增后读),多数情况下数字正确,但**限流的关键判断**在 server 那边 `if used_rpm > per_key_rpm` 是用返回的 `used_rpm` 决定的,如果计数丢失就允许超额请求通过;反之 SELECT 拿到另一个事务未提交的 count,会误判 429。

**[ratelimit.py:43-49]** `if used_rpm > self.settings.per_key_rpm: raise 429` 用的是返回值的 `int(row["count"])`,这是**字面意义上的"读别人写到一半的值"**。

**影响**:
- 限流形同虚设,或者反过来误封正常请求。
- 配额扣减也会错,影响计费 / 仪表盘。

**修复建议**:两步合并为一句,用 SQLite 的 `RETURNING` 子句(SQLite 3.35+):
```sql
INSERT INTO ratelimit_buckets (...) VALUES (..., 1, ...) 
ON CONFLICT(api_key_id, bucket) DO UPDATE SET count = count + 1
RETURNING count;
```
或直接用 Python 端原子操作:`UPDATE … SET count = count + 1 … RETURNING count`。

---

## P0-3 · `model_pool.refresh()` 和 `start()` 之间存在竞争窗口,endpoint 在 `start()` 之前用的是**自带** httpx 客户端,`_client` 共享被延迟到 `start()`,导致启动期间的所有请求走独立连接且 `aclose` 任务全部泄漏

**[model_pool.py:122-128, 243-249]**

```python
def __init__(...):
    ...
    self._client: Optional[httpx.AsyncClient] = None   # L122
    self.refresh()                                     # L125 — 同步调用 _rebuild_provider
                                                        # 但此时 self._client 还是 None

async def start(self) -> None:
    self._client = httpx.AsyncClient(timeout=httpx.Timeout(300))   # L244
    for ep in self.endpoints.values():
        self._rebuild_provider(ep)                     # L246 — 重新构建,这次共享 client
```

`refresh()` 在构造时立刻被调用,此时 `self._client is None`。`_rebuild_provider(L189)` 把 `client=self._client`(即 `None`)传进 `Provider.__init__`,**触发 `Provider._owned_client=True`**,每个 endpoint 各持一个独立 `httpx.AsyncClient` 和连接池。

`start()` 又再次 `_rebuild_provider(ep)`,每次都先调用旧 `aclose()`:
```python
if ep.provider_obj:
    try:
        asyncio.get_event_loop().create_task(ep.provider_obj.aclose())   # L192, 同步路径
    except Exception:
        pass
```

**问题**:
1. **`refresh()` 是同步方法,在 `__init__` 中调用,根本没有运行中的 event loop**(在 `uvicorn` lifespan 起来前就触发了)。`asyncio.get_event_loop().create_task(...)` 在没有 running loop 时会得到一个尚未启动的 loop 引用,该 task 实际上**永不执行**。
2. 旧 client 永远不会被关闭,**连接池 + socket fd 全部泄漏**。
3. 同样问题在 `remove_endpoint`(L238)和 `_apply_storage_overlay → _rebuild_provider`(L187)也存在 — 都是在请求热路径上调用,每次都泄漏一个 task + 一个 httpx client。

**影响**:
- 启动期间 hot reload 端点:泄漏 N 个 httpx 连接池(每个 pool 默认 100 keepalive)。
- 端点增删改频繁(尤其 WebUI 改配置)时,fd 会被吃光 → `OSError: Too many open files`。
- 重启时 `stop()` 调用 `await ep.provider_obj.aclose()`,但中途被 `refresh()` 替换的旧 client 已经被泄漏。

**修复建议**:
1. `_rebuild_provider` 改为返回旧 client 而非 fire-and-forget;`start()` 时统一 await 关闭。
2. 或者:`_rebuild_provider` 只更新 config,真实重建延后到下一次调用 `client` property 时 lazy 检测共享 client 变化。
3. 绝不要在 `__init__` 里 `create_task`,所有生命周期管理统一收敛到 `start()` / `stop()`。

---

## P0-4 · `bootstrap.spawn_child` 的日志文件句柄泄漏 + watchdog 重启风暴时累计打开

**[bootstrap.py:370-382, 454-457]**
```python
def spawn_child(cmd, log_file=None) -> subprocess.Popen:
    log_fh = open(log_path, "ab", buffering=0)   # L373 — 每次重启都 open 一次
    ...
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=log_fh, ...)
    return proc                                # log_fh 从未 close

def kill_proc_tree(proc, grace_seconds=5.0):
    # 只 kill 子进程,不 close log_fh
    ...

def _cleanup_all():
    if child_ref[0] is not None:
        kill_proc_tree(child_ref[0])            # 没 close log_fh
```

- `log_fh` 被传给 `Popen` 作为 stdout/stderr 后,**Popen 持有 fd 但 Python 端 reference 没保存**(局部变量)。
- `_cleanup_all` / `kill_proc_tree` 都没保存 log_fh 引用,无法关闭。
- watchdog 每次重启都 `spawn_child` 一次,稳定崩溃的子进程会导致 fd 累计。

**影响**:
- 长时间运行 + 偶发崩溃场景下 fd 累计(虽 Windows 下 Popen 退出时 OS 会回收,但 `spawn_child` 立即返回,中间的 Python GC 不确定时机)。
- watchdog 模式下稳定崩溃的故障循环:每次都新开一个 log_fh,旧 Popen 退出后 OS 关 fd,但 log_fh 这个 Python 对象可能晚于 Popen 退出才 GC,在高频重启期间堆积。

**修复建议**:
```python
def spawn_child(...):
    log_fh = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(cmd, stdout=log_fh, stderr=log_fh, ...)
    except Exception:
        log_fh.close()           # Popen 失败时关闭
        raise
    # 把 log_fh 绑到 proc 上,随子进程结束关闭
    proc._log_fh = log_fh
    return proc

def kill_proc_tree(proc, ...):
    ...
    if hasattr(proc, "_log_fh"):
        try: proc._log_fh.close()
        except Exception: pass
```
更进一步:**直接 `stdout=open(log_path, "ab")` 不持有 Python 对象**,改用 file name (`stdout=open(log_path, "ab")` 的实际就是 fd),并在 Popen 上挂 close-on-exec。

---

## P0-5 · `authenticate_api_key` / `verify_admin` 在 async 路径里跑 `bcrypt.checkpw`(12 rounds ≈ 200-300ms CPU),会阻塞 event loop

**[storage.py:200-210]** `verify_admin`:
```python
if not _bcrypt_verify(password, row["password_hash"]):    # bcrypt.checkpw, 12 rounds
```

**[auth.py:27-58]** `authenticate_api_key` 同样会落到 `storage.find_api_key` → `verify_admin`(如果走 admin token 路径)。`find_api_key` 用 SHA256(快),但 admin login (`/api/auth/login`) 一定命中 `verify_admin`。

**问题**:
- bcrypt 是**纯 CPU bound**,12 rounds 在普通 x86 单核 ≈ 200-300ms。
- 在 async FastAPI handler 里同步跑,会**阻塞整个 event loop** 期间所有请求排队。
- 配置热改密码时 `_bcrypt_hash` 12 rounds 在 admin API 里同步执行,期间整个进程不响应。

**影响**:
- 任何一次 admin 登录都会让 server 在 ~300ms 内**完全停止响应**(包括流式 chunk、heartbeat、watchdog 端口心跳)。
- 在 uvicorn 单 worker + 高并发下,管理请求和 chat 请求共用一个 loop,直接拖垮 chat 体验。

**修复建议**:
1. 密码校验挪到 `asyncio.to_thread(_bcrypt_verify, ...)` 或 `run_in_executor(ProcessPoolExecutor, ...)`。
2. 或者降到 10 rounds,并加一个进程级 bcrypt 互斥锁防并发 CPU 尖峰。
3. `find_api_key` 用 SHA256 已经够(API key 是高熵 secret),这条没问题,无需改。

---

# P1 — 高优

---

## P1-1 · `incr_rpm` 触发的 429 由"先增后查"导致,**配额耗尽那一次仍会计入**,可能误伤一两个请求

**[ratelimit.py:40-48]**
```python
used_rpm = self.storage.incr_rpm(key_id, bucket)
if used_rpm > self.settings.per_key_rpm:
    raise HTTPException(429, ...)
```

- `incr_rpm` 不管是否超限,先 +1 再判断。
- 第 61 次合法请求也被算入 61 > 60 → 触发 429。
- 实际上这是"已经超了",但严格地说是"刚超"的请求被拒绝。生产语义不友好。
- 配合 P0-2 的非原子读,会更乱。

**修复建议**:改成"先查后增"。如果连接是事务隔离 OK 的:
```python
row = SELECT count FROM ratelimit_buckets WHERE ...
if row and row["count"] >= per_key_rpm:
    raise 429
UPDATE ... count = count + 1 ...
```
或者直接:首次计数为配额 60,自减到 0 拒绝。

---

## P1-2 · `pool.call` 的 fallback 链路有 `await asyncio.sleep(min(2**attempt, 8))`,加上每个 provider 自身 120s timeout,**一次失败调用最坏要等 8 + 120s × 3 次 = 几乎无限**

**[model_pool.py:445, 451]** 每次失败后 await sleep;`model_pool.py:435` `await cur.provider_obj.chat(req)` 自身就是 120s。

**问题**:
- 一个 MoA 请求同时跑 4 个 reference(moa.py:215 `_run_references`),最坏情况下单次 MoA 耗时 = max(reference_timeout=60s) + aggregator_timeout=120s + critic_rounds × 120s。
- 一次 `/v1/moa/execute` 在最差路径下 = **8 分钟**。
- 长尾请求堆积:uwsgi/uvicorn 默认 keep-alive timeout 一般 5-60s,客户端断连时服务端还得跑完。

**影响**:
- 一个客户端取消连接,但 server 端 MoA pipeline 仍在跑(没有 cancellation propagation)。
- 高 RPS 下慢请求占着 worker,资源耗尽。

**修复建议**:
1. 在 server 入口注册 `request.is_disconnected()` 检查,断连则 cancel 当前 asyncio.Task。
2. 降低 default timeout(参考 P2-3)。
3. `asyncio.sleep` 在客户端已断开时应提前退出。

---

## P1-3 · 流式响应根本没有实现,但 OpenAI 兼容 schema 接受了 `stream=True` 参数并塞了空结果

**[server.py:187-325]** `chat_completions` 整体不区分 `req.stream`。代码中只在 `model_pool.py:413` 和 `openai_compat.py:21,123` 定义了 `chat_stream`,但 `server.py` 在 `pool.call(...)` 路径里强制 `stream=False`,从未用 `StreamingResponse`。

**[openai_compat.py:21-90]** `chat()` 内:
```python
if req.stream:
    # 走的也是同一个 chat(),只是多攒了 content_parts
    ...
```

**问题**:
- 客户端发 `stream=True`,server 把它当非流式处理,**所有 chunk 一次性返回**,违反 OpenAI 协议语义。
- 用 OpenAI SDK 流式调用的客户端会一直 hang 直到拿到完整 body(SDK 默认 timeout 10 分钟以上)。
- `chat_stream`(`openai_compat.py:123`)虽然存在但**全代码库无人调用**。

**影响**:
- 用户用 OpenAI 客户端(`stream=True`)调,体验非常差。
- 长上下文场景下,server 已经算完了全部内容,客户端才建立 chunked 接收 — 体验不及预期。
- 协议兼容性:这个 gateway 跟"OpenAI 兼容"的承诺不一致。

**修复建议**:
- 在 `chat_completions` 里:
```python
if req.stream:
    return StreamingResponse(_stream_response(...), media_type="text/event-stream")
```
- 真正调用 `provider.chat_stream` / 走 `StreamingResponse` 包装。
- 或者**显式拒绝 `stream=True` 并返回 400**,把责任说清楚。

---

## P1-4 · HTTP 200 但 body 是 error JSON 的情况**未识别**,直接 `data.get("choices")[0]` 抛出 IndexError

**[openai_compat.py:101-121]** 处理逻辑:
```python
if resp.status_code != 200:
    raise ProviderError(...)
data = resp.json()
choice = (data.get("choices") or [{}])[0]
message = choice.get("message") or {}
```

- 部分 provider(尤其是 OpenRouter / 自部署代理)在某些场景下会返回 `{"error": {"message": "..."}, "choices": []}`(HTTP 200)。
- 此时 `data.get("choices")` 是 `[]`,`(data.get("choices") or [{}])[0]` 是 `{}`,后面 `usage = data.get("usage") or {}` 不会崩,但 `message.get("content")` 返回空字符串 — 用户看到一个**200 OK 但内容为空**的 response。
- 这种情况既不会被 breaker 标记(`call` 路径只捕获 `ProviderError`),也不会被 retry,直接静默失败。
- 类似 `data.get("error")` 路径的 provider(如 Together / vLLM 在队列满时)也会被这样吃掉。

**影响**:
- 计量不准确(没有记 error log)。
- circuit breaker 不打开,持续走同一个坏 provider。
- 用户拿到空白回复,无从排查。

**修复建议**:
```python
data = resp.json()
if "error" in data and data["error"]:
    raise ProviderError(f"Provider returned 200 with error: {data['error']}",
                        status=data["error"].get("code") or 502)
choices = data.get("choices") or []
if not choices:
    raise ProviderError("Empty choices in response", status=502)
```

---

## P1-5 · `_check_one` 内 `_lock = asyncio.Lock()` 定义了但**从未被使用**,且 `_check_all_health` 在并发触达同一 endpoint 时会重复计数

**[model_pool.py:124, 277-312]**
- `self._lock` 定义但完全没用。
- `_check_all_health` 用 `asyncio.gather` 并行触发多个 `_check_one`,每个都直接改 `ep.consecutive_failures`、`ep.cooldown_until`、`ep.health_status`,**没有任何锁保护**。
- 在 `_check_all_health` 内部并发修改是安全的(每个 eid 独立),但**与请求路径的 `call()`**(L442-444)同时修改是 race:
  - `_check_one` 决定 "healthy → unhealthy" 改 `cooldown_until`
  - `pool.call` 中 `mark_failure` 也在改 `consecutive_failures`
  - 写读冲突:一个请求正在调 `is_available`(L82-86,读 cooldown_until)同时 health loop 在写。

**影响**:
- 并发改写 `consecutive_failures` 偶尔丢一次 +1 / -1(不是关键)。
- **breaker 状态机可能不一致**:health loop 觉得好了改回 healthy,但请求路径刚因为 401 把 cooldown 设到了 future,health loop 看到 cooldown_until<=now 又把 breaker 关闭了。

**修复建议**:
- 用 `self._lock` 把 `mark_success` / `mark_failure` / `recover_breaker` / `trigger_breaker` 包起来(都是短临界区)。
- 或者:把 `consecutive_failures` 等状态挪到 `dataclass` + 不可变 snapshot + atomic swap(乐观)。

---

## P1-6 · `ModelPool.endpoints` 是裸 dict,在 `upsert_endpoint` / `remove_endpoint` 中替换 endpoint 时没有锁,并发请求可能正在读 `pool.endpoints[eid]`

**[model_pool.py:209-231, 233-241, 128-167]**

`upsert_endpoint`:
```python
ep = ModelEndpoint(config=cfg)
self._rebuild_provider(ep)
self.endpoints[eid] = ep          # 直接覆盖
```

- 一个 chat 请求在 `pool.call(endpoint_id, ...)` (L418) 里:
  ```python
  ep = self.endpoints.get(endpoint_id)   # L418
  ```
- WebUI 同时调用 `POST /api/endpoints/{eid}/toggle`,内部 `pool.remove_endpoint(eid)`(L417) → `self.endpoints.pop(eid, None)`。
- 请求拿到 `ep` 后调用 `ep.provider_obj.chat(req)`,但 `ep.provider_obj` 在 toggle 路径上刚被 fire-and-forget `aclose()` 关闭(L238) → `httpx` 报 `RuntimeError: Client has been closed`。

**影响**:
- 偶发的 RuntimeError 在 chat 路径,触发 500。
- 用户配错 endpoint 重启后,旧请求崩。

**修复建议**:
- 对 `self.endpoints` 的所有读写包 `self._lock`(已有的 lock,只是没用)。
- `provider_obj.aclose()` 也 await,不要 fire-and-forget。

---

## P1-7 · `acquire/release` 的 SQLite `RLock` 是 `threading.RLock` 而非 `asyncio.Lock`,**对 event loop 没有任何保护作用**

**[storage.py:159]** `self._conn_lock = threading.RLock()`

- `conn()` 是 `@contextmanager`,**同步实现**,可在 `async def` 中直接 `with` — 但每次进入会 `sqlite3.connect` → 这本身是同步 syscall。
- 在 FastAPI 异步 handler 里调用 `storage.find_api_key(...)` 时:
  ```python
  with self.conn() as c:
      row = c.execute(...).fetchone()
  ```
- `c.execute` 是 sync SQLite 调用,会把 GIL 让出来,但 **fastapi 是单线程 event loop**,并没有其他线程;这其实没问题但**也没好处**(用不上多线程池)。
- 真正的问题:`conn()` 在 `await` 前后没有任何异步锁,并发请求在 `sqlite3.connect + execute + commit` 期间会被 OS-level file lock 卡住(P0-1 的根因之一)。
- `threading.RLock` 不能防止 async 并发的"同时进入" — 1000 个并发请求都拿得到(因为锁的是不同 thread)。

**修复建议**:
- 用 `asyncio.Lock` 保护 `conn()`(但需要先解决 P0-1,否则阻塞在 syscall)。
- 或干脆迁移到 `aiosqlite` / `SQLAlchemy async` 配合 `async with` + connection pool。

---

## P1-8 · `cleanup_old_logs` 存在但**从未被调用**,`request_logs` 无界增长

**[storage.py:449-453]** 定义了 `cleanup_old_logs(days)`,全文无引用。
- `config.py:55-56` `StorageConfig.log_retention_days: int = 30` 字段定义了但没人用。
- 高 QPS 场景:`request_logs` 每天 +N 万行 → 索引膨胀 → 查询 `/api/logs` 和 `aggregate_stats` 越来越慢。
- 没有 partition,也没有 vacuum。

**修复建议**:
- 加 `asyncio` 定时任务(可以放进 `lifespan`)每天跑一次 `cleanup_old_logs`。
- 或用 cron 外部触发。
- 配套 `VACUUM` 定期收缩 db 文件(注意 WAL 模式下手动 vacuum)。

---

## P1-9 · `incr_rpm` 不清理旧 bucket,`ratelimit_buckets` 表无界增长

**[storage.py:473-485]** 注释里也提到"sqlite 不会自动清理,生产可加 cron;暂保留"。

- 每天 1440 个 bucket/key,一个月就是 43200 行/key。
- 看起来小,但配合 P1-8 的 request_logs 增长,SQLite 的 free page 会越来越多,WAL 文件不会自动 shrink。
- 同时 `SELECT count FROM ratelimit_buckets WHERE …` 在没索引下需要 scan,虽然 `(api_key_id, bucket)` 是 PK 索引,这条没问题,但 PK 索引本身碎片化会拖慢。

**修复建议**:加一个 cleanup task:`DELETE FROM ratelimit_buckets WHERE updated_at < ?`。

---

## P1-10 · health_check 是 `GET /models` 或发小 payload,但对**所有 provider 都用同一个共享 client**,一个慢 provider 会阻塞所有 health_check

**[model_pool.py:281]**
```python
tasks = [self._check_one(eid) for eid, ep in self.endpoints.items()
         if ep.config.enabled and ep.config.api_key_runtime]
if tasks:
    await asyncio.gather(*tasks, return_exceptions=True)
```

- `gather` 本身是并发的,各 endpoint 的 health_check 互不阻塞(每个内部都有 `asyncio.wait_for(timeout_seconds)`)。
- 但 `OpenAICompatProvider.health_check`(openai_compat.py:177)用 `self.client.get`,**共享一个 httpx.AsyncClient**,共享的连接池上限(默认 keepalive 100)可能不够。
- 当 provider 数量 > 100 时,健康检查会争抢连接,反而把生产请求堵了。

**影响**:providers 多的场景下 health loop 自己变成流量源,挤占正常请求的连接。

**修复建议**:
- 给 health_check 走独立的 httpx client(`limits=httpx.Limits(max_connections=20)`)。
- 或给每个 provider 单独的小 client(回到 P0-3 的 leak 问题前先优化)。

---

# P2 — 改进

---

## P2-1 · `evaluate_complexity` 每个 chat 请求都重新算,正则在长 query 上是热路径

**[router.py:117-176, 547-568]**
- `evaluate_complexity` 跑 ~7 个正则 + 多次 substring 扫描(QUESTION_PATTERNS 60+ 个,TECH_KEYWORDS 60+ 个,MULTI_STEP_INDICATORS 20+ 个)。
- `_extract_keywords`(`moa.py:565-568`)在 `_calculate_consensus` 里用同样的正则,对每个 ref pair 都跑一遍。
- MoA 高质量模式跑 5 个 reference + 2 轮 critic:复杂度评分会被算 ~7 × (5 + 2) = 49 次。
- 单次评估 ~0.5-2ms(query 长 1KB 时),50 次就是 25-100ms 纯 CPU。

**修复建议**:
- 简单 LRU 缓存:`functools.lru_cache(maxsize=512)`,key = `(query, context_hash)`。
- 或预处理:把大 query 截断到前 4KB 评估(更细的精度对路由决策意义不大)。
- `_extract_keywords` 同样可缓存。

---

## P2-2 · `request_logs` 缺 `model_used` / `request_id` / `status` 索引,`aggregate_stats` 在大表上会全表扫

**[storage.py:419-447]**
```sql
SELECT COUNT(*), SUM(...), AVG(...) FROM request_logs WHERE timestamp >= ?
SELECT status, COUNT(*) FROM request_logs WHERE timestamp >= ? GROUP BY status
SELECT model_used, COUNT(*), SUM(cost) FROM request_logs WHERE timestamp >= ?
    AND model_used IS NOT NULL GROUP BY model_used ORDER BY n DESC LIMIT 10
```

- 现有索引只有 `(timestamp)` 和 `(api_key_id)`。
- `GROUP BY status`、`GROUP BY model_used` 都需要额外 sort + 全索引(或全表)扫。
- `aggregate_stats(days=7)` 在百万行表上可能 100ms+。

**修复建议**:
- `CREATE INDEX idx_request_logs_status_ts ON request_logs(status, timestamp)`
- `CREATE INDEX idx_request_logs_model_ts ON request_logs(model_used, timestamp)`
- 或预聚合:`request_stats_hourly` 表,后台每分钟 rollup 一次。

---

## P2-3 · `provider.timeout=120s` 太长,默认值没有保护手段

**[config.py:35]** `ModelEndpointConfig.timeout: int = 120`
**[model_pool.py:159]** 默认 120

- 一个请求最坏 120s 上限,但 MoA 一次请求可嵌套 5 + 2 轮 × 120s = 14 分钟(已见 P1-2)。
- `aggregate_stats` 的 `avg_latency_ms` 在 120s timeout 时方差极大,p99 几乎就等于 timeout,无法区分"真的慢" vs "timeout"。

**修复建议**:
- 默认降到 30s,允许 endpoint 级 override。
- MoA 内部每一层的 timeout 拆开,不要全继承顶层。

---

## P2-4 · `metrics.observe("chat_latency_ms", latency)` 只在 `deque(maxlen=200)`,p95/p99 在长尾请求下**根本不准确**

**[observability.py:73, 86-110]**
- `deque(maxlen=200)` 是 LRU 窗口,200 之前的旧数据直接丢弃。
- `n // 2`(p50)、`int(n * 0.99)`(p99) 都基于窗口内最近 200 次。
- 高 QPS 时这 200 次只覆盖几秒,p99 等于看"瞬时",不反映 SLA。
- 错误时(`metrics.error`)只增计数,没有"按 endpoint 维度"的细分。

**修复建议**:
- 至少分桶:`metrics.timings["chat_latency_ms"]` 用一个 ring buffer + 滑动窗口(60s / 5min / 1h)。
- 或者集成 prometheus_client 的 Histogram。
- 按 endpoint 维度拆分 timings,不然故障定位还是要看 log。

---

## P2-5 · `request_id` 没有跨模块传递,服务端日志看不到 trace

**[server.py:232, 304, 311-313; moa.py:157, 213, 276]**
- `request_id = "chatcmpl-" + uuid.uuid4().hex[:12]` 在 server 里生成,但传给 MoA 时换成了 `"moa_" + uuid.uuid4().hex[:12]`(`moa.py:157`)— **两个不同的 ID**,无法串起来。
- `observability.JsonFormatter` 支持 `record.request_id`,但没有任何代码用 `logger.info(..., extra={"request_id": ...})`。
- 客户端发的 `X-Request-ID` header 没有读取。

**修复建议**:
- 统一一个 `trace_id`(接受 `X-Request-ID`,否则生成),在 MoA 入口直接复用。
- logger 调用全部加 `extra={"request_id": rid}`。
- response header 回 `X-Request-ID` 给客户端。

---

## P2-6 · `asyncio.gather(*tasks, return_exceptions=True)` 在 `_check_all_health` 内部吞掉所有异常,只是 not log them

**[model_pool.py:281]** `await asyncio.gather(*tasks, return_exceptions=True)`

- `return_exceptions=True` 让异常当成结果返回,从不抛 — 这本身是 OK 的(避免一个 endpoint 健康检查失败影响其他)。
- 但**返回的异常根本没人用**,结果也没存。
- 等于:health check 出错只在 `_check_one` 内部的 `except` 块里打 log,如果 `_check_one` 内的 `try` 块在 await 之前就抛(比如 `self.endpoints.get(eid)` 返回 None 之后还在用)— 不可能,但是 `ep.provider_obj` 在 `_check_one` 内 `if not ep or not ep.provider_obj: return`,已经挡了。
- 真正问题:`return_exceptions` 的 `Exception` 对象被丢弃,日志里看不到 provider 失败的具体原因。

**修复建议**:gather 后用 `for r in results: if isinstance(r, Exception): logger.error(...)`。

---

## P2-7 · `bootstrap.heal.log` 每次 `_log` 都重新 `open(HEAL_LOG, "a", encoding="utf-8")`,频繁调用下文件句柄开销不必要

**[bootstrap.py:60-67]**
```python
def _log(msg, also_stdout=True):
    HEAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(HEAL_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
```

- `_log` 是 watchdog / 自愈路径热函数,每次重启 + 自愈步骤都会调用。
- 每次 with open 都 syscall,即便缓冲后其实没事,但**多进程并发写同一文件**(watchdog + 偶尔补刀的 heal)没有 fcntl 锁,可能交错写。

**修复建议**:模块级单例 `log_fh`,`atexit` 关闭。或用 `logging.handlers.RotatingFileHandler`(与 observability 一致)。

---

## P2-8 · `incr_tokens` 的 `raise HTTPException` 在**请求已经成功后才抛出**,导致 metrics / 日志记录不一致

**[ratelimit.py:51-61]**
```python
def incr_tokens(self, key_info, tokens):
    ...
    cur = self.storage.incr_daily_tokens(key_id, day, tokens)
    if cur > self.settings.per_key_daily_tokens:
        raise HTTPException(429, ...)
```

- 调用点:[server.py:260, 297] 在 chat 成功后调用。
- 已计入 metrics,日志说成功,但用户收到 429。**response 和 log 不一致**,告警系统会乱。

**修复建议**:配额检查放在请求开始时(预扣),或用 `asyncio.shield` 保护不抛 429 给成功的请求。

---

## P2-9 · `provider_obj.client` 的 lazy 创建路径**会绕过共享 client**

**[providers/base.py:65-69]**
```python
@property
def client(self) -> httpx.AsyncClient:
    if self._client is None:
        self._client = httpx.AsyncClient(timeout=self.timeout)
    return self._client
```

- `start()` 给所有 provider 传了共享 `self._client`,但**任何对 `client` 属性的访问,如果当时 `_client is None`(理论上不会,因为 `start()` 之后一定 not None),就会创建一个全新的私有 client**。
- 配合 P0-3 的 `_rebuild_provider` 问题,**在 start 之前的 endpoint**走的是这条 lazy 路径,而且**永远不会切换到共享 client**(直到下次 `start()` 重建)。
- 即便修了 P0-3,这条 fallback 仍然会在 `start()` 之前(导入阶段)被触发。

**修复建议**:让 `client` 永远先看 `self._client`,为空则 raise / lazy init with shared pool。

---

## P2-10 · `_lock = asyncio.Lock()` 在 `__init__` 创建,但 Python 3.10 之前在 non-running loop 下创建会有 deprecation warning,且 loop-bound

**[model_pool.py:124]** `self._lock = asyncio.Lock()`

- `asyncio.Lock()` 在 3.10+ 是 loop-agnostic 的(不绑定 loop),3.10 之前会绑定到调用时的 loop。
- 整个 `ModelPool.__init__` 是 import-time 调用,此时通常没有 running loop — 绑定到 import 时创建的 implicit loop,然后在 FastAPI lifespan 里换 loop → `RuntimeError: ... attached to a different loop`。
- 实际项目中用的可能是 3.11+,但仍是 anti-pattern。

**修复建议**:在 `start()` 内 lazy 创建 `self._lock = asyncio.Lock()`。

---

## P2-11 · `_run_critic` 的 `critic_messages` 拼接字符串里 ref 数量 × 内容长度未限,大模型 + 长上下文会让 `critic_messages` 轻易超过 50K tokens

**[moa.py:484-492]**
```python
critic_messages = [
    {"role": "system", "content": SYSTEM_CRITIC},
    {"role": "user", "content": (
        f"# 待审查的答案\n{current_content}\n\n"
        f"# 多模型参考(原始)\n"
        + "\n\n".join(f"【{r.model_id}】\n{r.content[:800]}" for r in ref_results if r.success)
        + "\n\n请按系统要求输出 JSON 评审结果。"
    )}
]
```

- 5 个 ref × 800 字符 ≈ 4000 字符 ≈ 1300 tokens,加上 `current_content` 本身可能 4000 tokens。
- critic 调用 max_tokens=1500,模型很可能"装不下" — 早期模型截断会很糟。
- 这是质量层 bug,但也直接拉长 critic 调用时间。

**修复建议**:
- 给 critic 喂**aggregated_content**就够了,refs 可以省略,或每个限到 400 字符。
- 或对 critic 也设 timeout,模型卡住时不无限等。

---

## P2-12 · 大量 `logger.info` / `logger.warning` 用 `%s` 格式化正确,但堆栈 `logger.exception` 在生产路径(每请求)调用过多

**[moa.py:449, 469; server.py:256, 290, 353]** `logger.exception(...)`

- `logger.exception` 自动 dump `sys.exc_info()`,生成堆栈。
- 在每次 chat 失败时打堆栈,5000 次失败就是 5000 个 traceback,日志文件会膨胀。
- 且 `logger.exception` 在 JSON formatter 下不会单独 dump stack(见 observability:25 `if record.exc_info: payload["exc"] = self.formatException(...)`),但 `formatException` 仍然遍历整个 traceback。

**修复建议**:
- 4xx/5xx 业务路径用 `logger.warning("call failed: %s", e, exc_info=False)`,只在真正 unexpected 时用 `logger.exception`。
- 加一个 sampling:每 100 次失败只 dump 1 次堆栈。

---

# 优先级矩阵

| 等级 | 数量 | 共同主题 |
|---|---|---|
| **P0** | 5 | SQLite 并发(P0-1/2/7),httpx 客户端泄漏 + event loop 锁(P0-3),watchdog fd 泄漏(P0-4),bcrypt 阻塞 event loop(P0-5) |
| **P1** | 10 | 限流语义/原子性,流式协议违约,200-with-error JSON,健康检查竞争,endpoint dict race,日志表/计数表无界增长 |
| **P2** | 12 | 正则热路径、metrics 窗口、trace_id 串联、log handle 频繁开关、超时默认值过大、配额抛错时机、critic prompt 体积 |

---

# 建议的修复路线

**第 1 周(消灭 P0)**:
1. 修 P0-1:`storage.py` 加 PRAGMA WAL + busy_timeout + 连接池(可换 `aiosqlite`)。
2. 修 P0-2:`incr_rpm` / `incr_daily_tokens` 用 `RETURNING`。
3. 修 P0-3 + P0-5:`_rebuild_provider` 返回旧 client 引用,`start()` 统一关闭;`bcrypt` 走 `to_thread`。
4. 修 P0-4:`spawn_child` 把 `log_fh` 绑到 `proc`,`kill_proc_tree` 关闭。

**第 2 周(消灭 P1)**:
1. P1-3 实现真正的 `StreamingResponse`(或拒绝 `stream=True`)。
2. P1-4 + P1-5 + P1-6 集中修:state 字段全加 `self._lock`。
3. P1-8 + P1-9:加一个 daily cleanup asyncio task。

**第 3 周起(P2 / 长期)**:
- P2-5 引入统一 trace_id,串起 server / moa / provider 三层。
- P2-1 + P2-2:加 LRU 缓存 + 索引。
- P2-4:`metrics` 升级到 prometheus_client(或自实现多窗口)。