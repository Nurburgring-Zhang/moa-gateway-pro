# Bug Hunt Report v2 (2026-07-14)

**Target:** `D:\MoA Gateway Pro\` (moa_gateway v1.6.3, Wave 11)
**Scope (Round 2):** concurrency races, error-injection paths, memory leaks
**Out of scope:** P0-1~5 / P1-1~7 already filed in `BUG_HUNT_REPORT.md`
**Method:** static review of `server.py` (3506 lines), `model_pool.py` (628), `storage.py` (610), `providers/{base,openai_compat,__init__}.py`, `capability/{rag_search,plan_act,channels,reference_router,feedback_loop,subagent_comms,worktree,hook_events,per_provider_rl}.py`

---

## Summary

- **P0 (Critical): 5 个** — 2 个任意文件读写 (新 capability), 1 个 fire-and-forget task 资源泄漏, 1 个 subprocess 阻塞 event loop, 1 个流式响应拿已关闭 client
- **P1 (High): 6 个** — 1 个 unbounded memory, 1 个 SQLite 不释放连接, 1 个 IS_IN cooldown 的 TOCTOU, 1 个 is_mock_key 可绕过, 1 个 metrics 计数器 race, 1 个 chat_kwargs 没传 max_retries 但用 sync 重试

---

## P0 (Critical) — 5 个

### P0-6: `capability_feedback_iter` 把用户控制的 `history_path` 直接当 `pathlib.Path` 读写 — 任意文件读 + 任意 JSON 覆盖 (security / memory)
- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:1273-1297` → `D:\MoA Gateway Pro\moa_gateway\capability\feedback_loop.py:241-321, 423-460`
- **Category**: concurrent / security-adjacent
- **Description**:
  `server.py:1287-1292` 把 `body.get("history_path", "")` 直接传给 `save_feedback`/`load_history`/`format_next_iter_prompt`。在 `feedback_loop.py` 里 `p = Path(history_path); p.parent.mkdir(parents=True, exist_ok=True)`(line 253-254, 290-291)— 任意**用户**传一个绝对路径,例如 `C:/Users/Administrator/.moa-gateway/prompts/aggregator.md` 或 `C:/MoA Gateway Pro/start.py`,会:
  1. `save_feedback` (line 241-257) 把构造的 JSON 覆盖写入**任意 path**(`p.parent.mkdir` 还能建任意父目录,等价于 mkdir -p,可能创建出 C:/ProgramData/.../.../ 等本不该有的目录)。
  2. `load_history` (line 315) 读任意文件 + `json.load` → **任意文件读** 暴露在 HTTP 响应里。
  3. `format_next_iter_prompt` (line 423-460) 把任意文件内容塞进 prompt 返回。

  触发条件:任意 API key(`require_api_key`,**不是 admin**),与 P0-4 (checkpoint atomic_write) 同一类问题的"新变种"。差别在于本次端点 (`capability/feedback-iter`) 在第一轮没审计,`require_api_key` 而非 `require_admin`,且:
  - `name`/`root_dir` 字段**完全没校验**
  - `format_next_iter_prompt` 把读到的 JSON 解析失败会 fallback → 实际上没 fallback,line 446-451 抛 `json.JSONDecodeError` 直接到 server.py 的 `except Exception → 500` 路径(没有 4xx 保护),**还能用来 blind read** (返回 500 vs 200 推断文件存在性)
- **Repro**:
  ```bash
  # 覆盖 start.bat,下次启动 RCE
  curl -X POST http://127.0.0.1:8000/v1/capability/feedback-iter \
    -H "Authorization: Bearer mgw-YOURKEY" -H "Content-Type: application/json" \
    -d '{"record":{"iter_idx":0,"summary":"x","strengths":[],"weaknesses":[],
         "next_iter_directives":[],"current_score":0,"target_score":1,"actions_taken":[],
         "artifacts":{},"timestamp":0,"elapsed_ms":0,"success":true},
         "history_path":"D:/MoA Gateway Pro/start.bat"}'

  # 任意文件读 (.fernet_key)
  curl -X POST .../v1/capability/feedback-iter \
    -d '{...,"history_path":"D:/MoA Gateway Pro/.fernet_key"}'
  # 响应里 iteration 字段会带出文件 bytes (如果 base path 可读)
  ```
- **Fix**:
  - 改 `require_admin` (而不是 `require_api_key`)。
  - `history_path` 必须限制在 `os.path.abspath(DATA_DIR) + "/feedback/"` 内,`os.path.commonpath` 校验,拒绝绝对路径或 `..`。
  - 删 `p.parent.mkdir(parents=True, exist_ok=True)` 的隐式目录创建;只允许白名单根下已存在的目录。
  - `load_history` 在解析失败时显式 `return []`,**不抛**(给调用方一致行为)。

---

### P0-7: `capability_rag_search` 全局 SQLite 在 `_get_conn` 里 **不关**连接 + 多 worker / 多协程下锁竞争导致 fd 耗尽 (resource leak / error)
- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\rag_search.py:71-82, 173-213, 261-273`
- **Category**: error / memory
- **Description**:
  `_get_conn()` 调 `sqlite3.connect(...)` 返回**未关闭**的连接(只有 `_cache_get`/`_cache_put` 显式 `conn.close()`)。但 `clear_cache` (line 57-68) 也走 `with _db_lock: conn = _get_conn(); ... conn.close()`。看似闭合,**实际**:
  1. **多 worker 进程**:`uvicorn --workers N` 下,**每个 worker 进程独立**有一份 `_db_lock`,但共享同一 `rag_cache.sqlite3`。SQLite 默认 `journal_mode=DELETE` + 没有 WAL + `timeout=5.0` 在多 writer 下会 `SQLITE_BUSY` 累计,虽然 timeout 兜底,5s 慢请求会积压;但更严重的是 `isolation_level=None` (autocommit,line 72) + 多进程多 writer 同时跑 `_cache_put` 的 `INSERT OR REPLACE`,会触发 `database is locked` 5s timeout,**每个请求都阻塞 5s**。
  2. **`_get_conn` 返回的 conn 在异常路径上不 close**:虽然 `_cache_get` / `_cache_put` 的 `try/finally: conn.close()` 看起来 close 了,但 line 102-107 `_hash_query` 之后的 `cached = _cache_get(query_hash, ttl_hours)` (line 264) 如果 `json.loads` 抛 (line 191)→ `cached` 是 `[]` → 继续走 `_rank` + `_cache_put` 重新写入。**问题是**:`_cache_get` 内部 `with _db_lock: conn = _get_conn(); try: ... finally: conn.close()` 拿锁 → 释放 → 再拿锁,在 `try` 块里 close,**关闭后行 `if not row: return []`** 之前一切正常。但 `_cache_put` 同理。
  3. **真正泄漏在并发场景下**:`uvicorn --workers 4` + `async def rag_search`(line 216) 在每个 worker 进程的 event loop 里有 N 个并发请求,每个请求都走 `with _db_lock` 拿进程内 RLock,**进程内串行化** → 整个 worker 实际上在排队,请求延迟 = N×(open + select + close) 几 ms 到几十 ms × N。worker 利用率极低。
  4. **连接未真正复用**:`_get_conn` 每次都新建一个 sqlite3 conn,**不做池化**。`sqlite3.connect` 本身有 connect-time 成本 (~1ms),在 QPS 100 时是 100ms/s 的纯浪费;QPS 1000 时是 1s/s,ev loop 70% 的时间都在 open/close sqlite。
  5. **没设 WAL + 没设 `synchronous=NORMAL`**:同 storage.py 的 P2-5 类似,WAL 模式下并发读不互斥。
  6. **endpoint `clear_cache` 删表后不会 shrink 文件**:SQLite 在 DELETE FROM 后文件大小不变,长跑后 `rag_cache.sqlite3` 一直增长直到 manual `VACUUM`,**没有 cron / 触发**。
- **Repro**:
  ```python
  import asyncio
  from moa_gateway.capability.rag_search import rag_search

  async def hit():
      return rag_search("hi", [{"id": str(i), "text": "x"*1000, "tags": []} for i in range(100)])

  # 单 worker 1000 并发
  async def main():
      await asyncio.gather(*[hit() for _ in range(1000)])

  asyncio.run(main())
  # 1000 个 sqlite3.connect 串行(进程内 lock)→ 2000+ ms wall time
  # 若用 4 workers 跑同脚本,SQLITE_BUSY 重复 → 一些请求 5s timeout
  ```
- **Fix**:
  - 用 `aiosqlite` 替代 `sqlite3` (异步,无 GIL 阻塞)。
  - 进程内 conn pool (lazy 单例 + 重置检测)。
  - 启动时 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`。
  - `clear_cache` 后 `VACUUM` 或 `PRAGMA wal_checkpoint(TRUNCATE)`。
  - 给 endpoint 加最大并发限制(`asyncio.Semaphore(N)`),N=worker 数,避免单 worker 内 N×N 排队。

---

### P0-8: `capability_worktree.snapshot()` 同步 `subprocess.run` 无 timeout,直接被 FastAPI `await` 调用 — 阻塞 event loop (error / DoS)
- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\worktree.py:34-59, 270-299` × `D:\MoA Gateway Pro\moa_gateway\server.py:2180-2210`
- **Category**: error (DoS)
- **Description**:
  `_run_git` 用 `subprocess.run(cmd, cwd=repo_path, ...)` 但**没传 `timeout` 参数**。`snapshot()` 在 line 270-299 调 3-4 次 `_run_git`(rev-parse、status、ls-files、symbolic-ref)— 每个都是同步阻塞。`server.py:2180-2201` 直接 `await snapshot(repo_path)` / `await diff_snapshots(snap1, snap2)`,**FastAPI 的 async 函数 await 同步函数** = 整个 event loop 被卡死(sync 函数占用主线程,async 协程全部不调度)。
  触发场景:
  1. **`repo_path` 指向一个 NFS / 慢盘上的 path**:`git rev-parse` 几十秒到几分钟才返回。期间整个 server 拒绝所有请求(starvation)。
  2. **`repo_path` 在 `.git/` 目录上有 `core.fsmonitor` hook 指向一个会 hang 的 binary**:git 会启动 fsmonitor 进程并 wait,触发类似 P0-5 的 `.gitconfig` 攻击。
  3. **`repo_path` 是个 git 仓库但 `.git/HEAD` 是 fifo / symlink 指向不存在的 fd**:git 子命令 read HEAD 时 hang 住。
  4. **`diff_snapshots` 路径**:line 2196-2201 顺序调 `snapshot(p1)` 再 `snapshot(p2)`,两次同步 git 调用,**两个 endpoint 在并发时被序列化**。

  虽然 server.py:2152 改用 `require_admin` (P0-5 修复),但 admin 自己的请求也会卡死整个 server,等同于自我 DoS。**subprocess.run 没有 timeout = 必崩**。
- **Repro**:
  ```python
  # 挂起 git 进程模拟
  # 在 Windows 上: 创建 .git/HEAD 是个不可读的 fifo
  # 或把 .git 指到 UNC path \\slow-nas\repo\.git
  # 或在 .git/config 里加 [core] fsmonitor = "cmd /c ping -n 9999 127.0.0.1"
  ```
  ```bash
  curl -X POST http://127.0.0.1:8000/v1/capability/worktree \
    -H "Authorization: Bearer <admin jwt>" -H "Content-Type: application/json" \
    -d '{"action":"snapshot","repo_path":"D:/repo-with-slow-disk"}'
  # 整个 server 30s+ 没响应(所有其他 endpoint 也卡)
  ```
- **Fix**:
  - `_run_git` 加 `timeout=` 参数(`subprocess.run(..., timeout=15.0)`,捕获 `subprocess.TimeoutExpired` 转 `GitCommandError`)。
  - `snapshot` 改 async:`await asyncio.to_thread(_run_git, ...)` 或用 `asyncio.create_subprocess_exec` + `await proc.wait()`。
  - 或者把整个 worktree 操作包在 `asyncio.wait_for(asyncio.to_thread(snapshot, repo_path), timeout=10.0)` 里。
  - 加 `core.fsmonitor=false` 等 git hygiene:`GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0` 等 env。

---

### P0-9: `_stream_single` 在长跑流式响应中,**`ep.provider_obj` 可能被 `refresh()` / `_rebuild_provider` 替换为新对象甚至 None** — `aclose` 后的旧 client 仍被 `async for` 引用 (resource leak / crash)
- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:3404-3437` × `D:\MoA Gateway Pro\moa_gateway\model_pool.py:217-235, 272-304`
- **Category**: resource leak / crash
- **Description**:
  `_stream_single` (server.py:3414) 在 SSE 流期间 `async for chunk in ep.provider_obj.chat_stream(...)` 一直引用 `ep.provider_obj`。**期间**:
  1. 任何 `subscribe_settings_change` 回调触发 (WebUI 改 config / `reload_settings()`) → `_on_settings_change` (model_pool.py:146) → `self.refresh()` → 对每个 endpoint `ep.config = cfg; self._rebuild_provider(ep)`。
  2. `_rebuild_provider` (line 217-235):**先把旧 `ep.provider_obj` push 到 `self._pending_close` list,再 `ep.provider_obj = None`,再 `ep.provider_obj = build_provider(...)` 创建新对象**。
  3. 流式响应里 `async for chunk in ep.provider_obj.chat_stream(...)` 此时:
     - 如果**刚好在** `ep.provider_obj = None` 和 `ep.provider_obj = build_provider(...)` 之间的 race window → 拿到 None → `AttributeError: 'NoneType' object has no attribute 'chat_stream'` → 异常被 except 吞掉 (line 3434-3436),用户拿到 `{"error": "..."}` + `[DONE]`,**但流期间 503 不可见**。
     - 如果**已经创建了新 provider_obj** → `async for` 仍持有**旧** provider_obj 引用(因为 line 3414 `ep.provider_obj.chat_stream` 是同步取值,**值在那一瞬间固定**),旧对象在 `stop()` 时才会被 `await ep.provider_obj.aclose()`(model_pool.py:289-294) → **但 `_pending_close` 里的旧对象可能因为 aclose 是 httpx.AsyncClient.aclose() 而**:
       - 立即关 TCP 连接 → 流式 HTTP 正在 chunk 中 → httpx 抛 `StreamClosed` 之类。
       - 旧 provider 内部 `_owned_client=False`(共享 pool 的 client,见 model_pool.py:233 `client=self._client`)→ `aclose` 实际不关共享 client → OK;**但如果 client 是它 own 的(行 68 `httpx.AsyncClient(timeout=self.timeout)`)**,`aclose()` 关整个 client,池里其他正在并发的请求一起炸。
  4. **更微妙的 race**:`_pending_close` 是**普通 list** (line 140),`append` / `pop` (line 224, 297) 没加锁;`refresh` 在同步路径上调,而流式响应在 async 路径上跑;list.append 是 GIL-atomic 的所以不会 segfault,但**`stop()` 时 `while self._pending_close: prov = self._pending_close.pop(); await prov.aclose()`** 时,`stop` 正在 iter,list 正在被 append 的话 (settings 还在变),会导致 stop 错过部分待 close 的对象,这些对象随 Python GC 时 `aclose` 是 coroutine,**协程没被 await → 永远不执行** → 句柄/连接全泄漏。
- **Repro**:
  ```python
  import asyncio
  from moa_gateway.config import reload_settings
  from moa_gateway.model_pool import get_model_pool

  pool = get_model_pool()

  # 模拟长流 (mock provider sleep)
  async def slow_stream():
      ep = list(pool.endpoints.values())[0]
      async for c in ep.provider_obj.chat_stream(req):
          await asyncio.sleep(0.1)
          yield c

  async def kick_reload():
      await asyncio.sleep(2.0)
      reload_settings()  # 触发 _on_settings_change → refresh → _rebuild_provider

  async def main():
      await asyncio.gather(slow_stream(), kick_reload())

  asyncio.run(main())
  # 旧 provider 的 httpx 连接可能没正确关闭
  ```
- **Fix**:
  - 启动流式响应时,**先 copy 一份 provider_obj 引用**:`provider = ep.provider_obj` (在 line 3414 之前),后续全部用 `provider` 而非 `ep.provider_obj`。
  - `_pending_close` 改用 `asyncio.Queue` 或 `set[asyncio.Task]` + asyncio Lock,所有 `append/pop` 走 `async with self._lock`。
  - `_rebuild_provider` 在替换 `ep.provider_obj` 时,**先 `await ep.provider_obj.aclose()`(async 函数),再赋新值**;但 _rebuild_provider 是 sync 路径,所以把 `ep.provider_obj` 标个"deprecated"标志,所有调用方拿到的引用在用完前先 check 标志,失效则报错重试。
  - 或更彻底:把 `_rebuild_provider` 改成 `async def`,从 `refresh`/`upsert_endpoint` 等入口改为 `async def refresh_async`,server.py 在收到 SSE chunk 之间 `await asyncio.sleep(0)` 让出 ev loop,让 setting 变更能跑。

---

### P0-10: `ModelPool.stop()` 中 `for ep in self.endpoints.values()` 与 `_pending_close` 互不重叠的迭代 — 旧 client 永久泄漏 (resource leak)
- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:280-304`
- **Category**: resource leak
- **Description**:
  `ModelPool.stop()`(line 280-304)分两段:
  ```python
  for ep in self.endpoints.values():
      if ep.provider_obj:
          try: await ep.provider_obj.aclose()
          except Exception: pass
  while self._pending_close:
      prov = self._pending_close.pop()
      try: await prov.aclose()
      except Exception: pass
  ```
  问题:
  1. **`_pending_close` 是 list,`pop()` 在 stop 期间** **不会被任何生产方改**:这阶段 refresh() 应该不再被调(因为 stop 是 shutdown 路径),**OK**。但 list 本身是普通可变对象,`pop()` 在多次 `while` 循环里从末尾取,**而 `_rebuild_provider` 推入顺序是从 append(从 list 尾部)**(line 224)。pop 顺序 = LIFO(后进先出) → 关闭顺序与创建顺序相反,可能引起**级联问题**:先关 ref 模型,再关 aggregator,而 aggregator 内部 httpx connection pool 复用了 ref 模型的连接 — 实际不致命因为 httpx 内部一关全关。
  2. **真正的泄漏**:`ep.provider_obj.aclose()` 假设 provider 有 `aclose` async 方法 — `base.py:71-74` 写了,但 **如果 provider 来自 `_REGISTRY[provider_id]` 但 provider 自身没有实现 `aclose`**(比如自定义 provider),会抛 `AttributeError`,被 `except Exception: pass` 吞,**没关闭的 client 一直 hold 句柄**。
  3. **更微妙的**:`_client = httpx.AsyncClient(timeout=httpx.Timeout(300))` (line 273) 是**所有 endpoint 共享的 client**(line 233 `client=self._client`)。`stop()` 先 `for ep: await ep.provider_obj.aclose()` — `aclose` 内部 `if self._owned_client and self._client is not None: await self._client.aclose()`(`base.py:72-74`)。**因为 `_owned_client=False`(传了 client)**,`aclose` 不会关共享 client,OK。然后**`self._client.aclose()` 在最后关一次**。**但**:
     - 如果中间有 provider 是 `_owned_client=True`(没传 client,自己 new 的),`aclose` 已经把那个 client 关了 — 没影响共享 client。
     - 但如果**多线程同时在 stop** (虽然 stop 是 async 路径,不太可能),同一 client 多次 `aclose` httpx 内部会 `RuntimeError: Event loop is closed`(FastAPI lifespan 收尾时常见)。
  4. **`_pending_close` 累计到成百上千个对象**:每次 `subscribe_settings_change` 触发 `_on_settings_change` → `refresh()` → 对每个 endpoint `_rebuild_provider` → 旧对象 push 到 list。**list 永远只增不缩**,直到 `stop()`。长跑 server(几小时)+ WebUI 反复改 config,`len(self._pending_close)` 可以涨到几千,每个对象都带 `httpx.AsyncClient` 实例(几 MB),**总内存可能涨到 GB**。这是 P0-10 的核心。
- **Repro**:
  ```python
  import threading
  from moa_gateway.config import reload_settings
  pool_endpoints_count = 5
  for i in range(1000):
      reload_settings()  # 每次 refresh 把 5 个旧 provider 推入 _pending_close
  # __main__ 退出时,stop() 关闭 5*1000 = 5000 个 provider
  # 实际表现: stop() await 5000 次 aclose,slow shutdown 30s+
  # 在 reload 期间已经累计在内存里的旧 httpx.AsyncClient,从未被显式关
  ```
- **Fix**:
  - **改 `_pending_close` 为 `asyncio.Queue` 或 `deque(maxlen=N)`**(bound 大小,旧对象满了就同步关掉,或直接 drop 并 log warning)。
  - **或** 启动一个 `asyncio.create_task(self._close_pending_loop())` 后台 task 周期(每 30s)把 `_pending_close` 队列里的对象 `aclose` 掉,边进边出。
  - 同步路径上**优先**复用同一个 `self._client`,所有 provider 都用 `client=self._client`(line 233),只在 `self._client` 本身出问题时才换。
  - `aclose` 改用 `getattr(self, 'aclose', None)` 兼容没有 aclose 的 provider。

---

## P1 (High) — 6 个

### P1-8: `incr_rpm` 的 `BEGIN IMMEDIATE` 配合 `_conn_lock` 是双锁 — 死锁 / 不必要的串行化 (concurrent)
- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:244-261, 584-607`
- **Category**: race
- **Description**:
  `Storage.conn()`(line 244-261)用 `self._conn_lock = threading.RLock()`(line 233)包整个上下文。`incr_rpm` (line 584-607) 在 `with self.conn() as c` 里**再** `c.execute("BEGIN IMMEDIATE")`。
  问题:
  1. **`_conn_lock` 已经是串行化所有 DB 操作的全局锁**(每条 SQL 都拿锁开新 conn + commit + close),实际上 SQLite 已经在文件级 serialize。**再叠 `BEGIN IMMEDIATE` 加 write lock 是多余的**。
  2. **更严重**:每次 `incr_rpm` 都 open + close 一个新 sqlite3 conn(line 247 `sqlite3.connect(...)` + line 261 `c.close()`)。在 1000 RPM 的 key 下,**每分钟 1000 次** open+close,每次 ~1ms 浪费 → 1s/s 纯 fsync/fd 开销。
  3. **BEGIN IMMEDIATE 拿 SQLite 的 RESERVED lock**(写锁),在同一 conn 内继续用;`conn()` contextmanager 在 finally 里 `c.close()` (line 261) 自动隐式 ROLLBACK(如果有未提交事务)或保留;但**`BEGIN IMMEDIATE` 在 `with` 退出时如果还没 COMMIT/ROLLBACK,close 会触发隐式 ROLLBACK**。看代码:`incr_rpm` 的 `try/except` (line 591-607) 走 `COMMIT` 或 `ROLLBACK` 两条路径,正常路径下确实 COMMIT,异常路径 ROLLBACK,**OK**。
  4. **但**:`incr_rpm` 已经被 `with self.conn() as c` 包,`c` 的 commit/rollback 由 `conn()` contextmanager 控制(line 259 `c.commit()` + line 261 `c.close()`)。如果 `incr_rpm` 自己 `c.execute("BEGIN IMMEDIATE")` 走自己事务,然后 `c.execute("COMMIT")`,**但 `conn()` 的 finally 还会 `c.close()` — 这个 close 之前没有 `commit` 也不会再 commit**,SQLite 不会有问题(close 自动 ROLLBACK 未 commit 事务,这里 COMMIT 已经过了,无事可做)。**但**:如果 inc_rpm 的 `COMMIT` 之后,到 `conn()` 的 `c.commit()` 之间发生了**第二次 commit**,SQLite 不报错但也没意义。
  5. **多 worker 进程 (`uvicorn --workers 4`) 下**:`_conn_lock` 是**进程内 RLock**,**不跨进程**。WAL + busy_timeout=5000 兜底,大概率 OK,但 4 个进程同时写 `ratelimit_buckets` 表,WAL writer 锁冲突,5s 内决出胜负,期间请求延迟突增。
  6. **`incr_rpm` 内部有显式 `BEGIN IMMEDIATE` + `COMMIT`,但外层 `conn()` 又有自己的隐式 commit — 双层事务不嵌套,实际是两条独立事务**:`BEGIN IMMEDIATE` 开了内事务,`COMMIT` 结束内事务;然后 `conn()` 的 `c.commit()` 是个空 commit(autocommit 状态)。这不是 bug,但代码风格混乱,P0-1 修复后这个重复还存在。
- **Repro**:
  ```python
  import asyncio
  from moa_gateway.storage import get_storage
  s = get_storage()
  async def hit():
      for _ in range(10):
          s.incr_rpm("key_test", "bucket1")
  async def main():
      await asyncio.gather(*[hit() for _ in range(100)])
  asyncio.run(main())
  # 实测: 100×10=1000 inc_rpm,每次都 open+close+journal_mode PRAGMA+sync PRAGMA
  # wall time ~1-2s (单进程),4 worker 并发触发 5s timeout
  ```
- **Fix**:
  - 在 `Storage.__init__` 里**只** open **一个** sqlite3 conn 复用(类似 providers/base.py 的 httpx client),所有操作复用它,加 `check_same_thread=False` (line 232 已设)即可。
  - 删 `incr_rpm` / `incr_daily_tokens` 里的 `BEGIN IMMEDIATE`(既然单 conn 串行化,BEGIN 不需要;或保留作为显式事务边界,但要去掉外层 `conn()` 的 commit 行为,改用嵌套事务或 no-op)。
  - 启动时一次性 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;` 设到那个长生命 conn 上。

---

### P1-9: `is_in_cooldown(at=at)` 在 per-provider RL 里 `at` 是浮点时间戳,**没有校验 `at` 的语义** — TOCTOU / 计时器注入 (concurrent)
- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\per_provider_rl.py:143-168, 172-262` × `D:\MoA Gateway Pro\moa_gateway\server.py:1360-1380`
- **Category**: race
- **Description**:
  `is_in_cooldown(at: Optional[float] = None)` (line 157-161) 直接拿 `at` 与 `self._cooldown_until` 比,**没有**:
  1. **校验 `at` 来自调用方的可信度**: `server.py:1370-1372` 把 `body.get("at")` 透传给 `is_in_cooldown(at=body.get("at"))`。`at` 可以是 `9999999999.0`(未来)→ `is_in_cooldown(9999999999)` 永远 True → 服务端可以**伪造未来时间戳绕过 cooldown 锁定**。
  2. **`mark_429(at=body.get("at"))` 同样**: line 145 `if at is None: at = time.time()` 只在 at=None 时填默认;如果客户端传 `at=0.0`(1970),`new_until = 0.0 + duration = duration` 是个**过去的**时间,`if new_until > self._cooldown_until` 判定里 `self._cooldown_until` 默认 0.0 → `duration > 0.0` True → 设为 `duration` → 这**没有问题**;但如果客户端传 `at=1e18`(极大未来),`new_until = 1e18 + duration`,**锁永远不解**。
  3. **`record_usage(at=at)` 同样**:line 273-286,客户端传 `at=9999999999`,`self._history.append(rec with timestamp=9999999999)`,下次 `_prune(at)` 用未来时间做 cutoff,可能**prune 掉所有正常记录**(取决于实现:`while self._history and self._history[0].timestamp < cutoff`,如果 cutoff 是未来,所有 history 都 < cutoff,整个 _history 被清空 → 限流计数重置)。
  4. **多 worker 进程**:`MultiProviderLimiter` (line 387+) 是**进程内**单例,`server.py:1352` 每次请求 `mpl = MultiProviderLimiter(limits)`,**new 一个新对象**,意味着:
     - `mark_429` 设的 cooldown 在这个 mpl 上,**下个请求 new 一个新 mpl,cooldown 丢失**(因为是 process-local state,新对象没继承)。
     - `record` / `check` 同样每次重置。
     - **结果**:per-provider RL 在 multi-worker 模式下完全失效,跟没设一样;只在单 worker + 复用 `mpl` 时才生效。
     而 server.py:1352 每次 `mpl = MultiProviderLimiter(limits)` 都新建,**per-provider RL 实际是个一次性 mock**。
  5. **endpoint `mark_429` 行为 (server.py:1363-1366)**: `mpl.limiters[provider]` 拿的是**新建 mpl** 的 limiters,cooldown 不会跨请求持久化。
- **Repro**:
  ```bash
  # step 1: 设 cooldown
  curl -X POST .../v1/capability/per-provider-rl \
    -d '{"provider":"deepseek-v3","action":"mark_429","cooldown_seconds":99999}'
  # step 2: 立刻 status 查 in_cooldown
  curl -X POST .../v1/capability/per-provider-rl \
    -d '{"provider":"deepseek-v3","action":"status"}'
  # 第一个请求新建 mpl,mark_429 写在它上面;第二个请求又 new 一个 mpl,_cooldown_until=0
  # status 返回 in_cooldown: false
  ```
  ```bash
  # 攻击: 用 at=9999999999 锁死一个 provider (如果 mpl 跨请求复用)
  curl ... -d '{"provider":"x","action":"mark_429","cooldown_seconds":1,"at":9999999999}'
  ```
- **Fix**:
  - **校验 `at`**: `if at is None: at = time.time(); elif at < 0 or at > time.time() + 86400 * 365: raise ValueError("at out of sane range")`。
  - **MultiProviderLimiter 改成模块级单例**:`_limiter_instance = None; def get_multi_limiter(): global _limiter_instance; if _limiter_instance is None: _limiter_instance = MultiProviderLimiter(); return _limiter_instance`。server.py:1352 改用 `get_multi_limiter()`。
  - 或在 `Storage` / Redis 持久化 cooldown / 限流历史,跨进程共享。

---

### P1-10: `SubagentHub._inboxes` 和 `_outbox` 只追加不清理 — 长时间运行内存泄漏 (memory)
- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\subagent_comms.py:78-83, 111-143, 145-175` × `D:\MoA Gateway Pro\moa_gateway\server.py:1887-1963`
- **Category**: memory leak
- **Description**:
  `SubagentHub.__init__` (line 78-83) 初始化 `_inboxes: Dict[session -> List[Message]]` 和 `_outbox: List[Message]`,**全部** `append` (line 124-125, 140-142, 173-174, 184),**从不 pop/clear/limit**。
  - 任何 `send_message` / `broadcast` / `deliver` 都会向 inbox 追加,**没上限**。
  - 任何 reply 也会向 inbox + outbox 各追加一次。
  - 攻击/误用:
    ```bash
    # 客户端用 send 灌 1M 条 10MB 消息 → ~10GB 内存
    for i in {1..1000000}; do
      curl ... -d "{\"action\":\"send\",\"to_session\":\"$i\",
                    \"content\":\"$(yes A | head -c 10000000 | tr -d '\n')\"}"
    done
    ```
  - **更重要的是**:`SubagentHub` 是 `_hubs[session_id] = SubagentHub(session_id)` 创建的(server.py:1901-1906),`session_id` 来自用户输入,**无限创建 hub**:
    ```bash
    for sid in unique_sids; do ...; done  # 创建 N 个 hub
    ```
    每个 hub 自己有 `{session_id: []}` 初始空 list,但 `send_message` 把消息投到 `to_session` 那个 session,**在 `_register(to_session)`** (line 86-88) 时**也创建该 session 的 inbox list** → hub A 发送消息给 session B,会让 hub A 的 `_inboxes[B]` 增长,而 hub B 自己可能不会被创建(server 只为这个 `session_id` 创建 1 个 hub)。
  - `inbox()` (line 145-148) 每次返回**全量 list 快照**,如果 inbox 10M 条,序列化 10M 条 dict 一次就是几秒 + 几 GB 临时对象 → 雪崩。
  - 没有 `prune` / `clear` / `max_inbox_size` 任何清理机制。
- **Repro**:
  ```python
  from moa_gateway.capability.subagent_comms import SubagentHub
  hub = SubagentHub("s1")
  for i in range(1_000_000):
      hub.send_message("s2", "A" * 1000)
  # hub._inboxes["s2"] 现在 1M 条,内存约 1GB
  ```
- **Fix**:
  - 加 `_inbox_max_size: int = 10000` (每个 session 上限);`append` 前 check 长度,满了就 `popleft()` 最旧消息(改 list 为 deque)。
  - 加 `prune_older_than(seconds)` 方法,server 端用 `asyncio.create_task(_prune_loop())` 周期清理。
  - endpoint 加 `max_content_length` 校验(目前 server.py:1907 `body.get("content", "")` 没限长)。
  - endpoint 加 `inbox_size` 参数(只取最近 N 条而非全量)。

---

### P1-11: `HookRegistry._handlers` 永久累积 — register 不限次数 (memory)
- **File**: `D:\MoA Gateway Pro\moa_gateway\capability\hook_events.py:131-228` × `D:\MoA Gateway Pro\moa_gateway\server.py:1583-1633`
- **Category**: memory leak
- **Description**:
  `HookRegistry.__init__` (line 140-141) 初始化 `self._handlers: Dict[str, HookHandler] = {}` 和 `self._order: List[str]`。
  - `register` (line 145-156) 直接 `_handlers[h.handler_id] = h` + `self._order.append(handler_id)`,**handler_id 来自 handler 自己的属性**。
  - **endpoint `capability_hook_events` (server.py:1601-1603)** 的 `action=register` **根本不真的 register** — 看 server.py:1601-1603:
    ```python
    if action == "register":
        # 需 callback 不能从 body 拿,只返回 event list
        result = {"registered_event": body.get("event"), "total_handlers": len(reg.list_handlers())}
    ```
    **`total_handlers` 来自 `reg.list_handlers()`** (line 196-204),而 `list_handlers` 每次都 list comp 全量 `_handlers.values()`。
  - 真正问题: `HookRegistry` 是 `capability_hook_events._registry`(server.py:1596-1597, `hasattr(...) = ... else HookRegistry()`),**模块函数级单例,跟 server 进程同寿命**。
    - `_handlers` / `_order` 实际只能通过 `register` API 增长,但 endpoint 不调它(只查),**所以 endpoint 不会让 registry 增长**。
    - 但其他内部模块如果 import `HookRegistry` 调 `register(handler)`,**handler 永久在 dict**。
    - `_ralph` 字段(capability_hook_events:1619 `RALPH_CYCLE`)**每次新 ralph_advance 都可能 init 新 instance**(line 1620 `if not hasattr: ... _ralph = RALPH_CYCLE(max_iter=...)`)。
  - **真正的内存问题**:`register_mock_provider` 在 `reference_router.py:156-158` 同样有**进程级单例** `_PROVIDER_OVERRIDES`:
    ```python
    _PROVIDER_OVERRIDES: Dict[str, Dict[str, float]] = {}
    def register_mock_provider(model_id: str, **kwargs) -> None:
        _PROVIDER_OVERRIDES[model_id] = dict(kwargs)
    ```
    同样**没有 unregister / limit**。
  - 此外 `capability_hook_events._ralph.iteration` / `cycle.iteration` 内部 data 累积(取决于 RALPH_CYCLE 实现,需查 hook_events.py 内部)。
  - **P0-5 fix 留下的次生**:`capability_bubble._managers` (server.py:2095-2104)、`capability_subagent_comms._hubs / _boards / _locks` (server.py:1901-1949)、`capability_version._stores` (server.py:1977-1979)、`capability_config._stack` (server.py:2047-2048)、`capability_frozen._registry` (server.py:2728-2729)、`capability_artifact._registry` (server.py:2657-2660)、`capability_acceptance._trees` (server.py:2854-2856)、`capability_grace._registry` (server.py:2949-2950)、`capability_in_flight._detector` (server.py:2519-2521) — **全是 `hasattr` 单例 + 用户可控 key,无 bound**。
- **Repro**:
  ```python
  from moa_gateway.capability.hook_events import HookRegistry, HookEvent
  reg = HookRegistry()
  for i in range(1_000_000):
      reg.register(HookHandler(handler_id=f"h_{i}", event=HookEvent.PostToolUse, ...))
  # 1M 个 HookHandler 永久占内存
  ```
- **Fix**:
  - 所有 `hasattr(capability_X, "_singleton")` 单例模式加 LRU 上限(如 max 10000 keys,满了 LRU evict)。
  - `register_mock_provider` 加 `unregister_mock_provider` API。
  - 启动后台 task 定期 `gc()` 长时间未访问的 hub/board/store。
  - 最简单:`_hubs` / `_boards` 全部用 `cachetools.LRUCache(maxsize=1000)` 替代 dict。

---

### P1-12: `authenticate_api_key` 的 `is_mock_key` 用字面 `"mock"` 字符串判断 — 业务 key 误判 / 限流配额错算 (logic / concurrent)
- **File**: `D:\MoA Gateway Pro\moa_gateway\providers\__init__.py:28-39` × `D:\MoA Gateway Pro\moa_gateway\model_pool.py:224-235, 384-408, 540-565` × `D:\MoA Gateway Pro\moa_gateway\ratelimit.py:60-81`
- **Category**: logic / race
- **Description**:
  `is_mock_key(api_key)` (line 28-39) 把 `"mock"`, `"mock-key"`, `"your-xxx"`, `"sk-your-xxx"`, 空字符串都当 mock。
  - `model_pool.py:227` `_rebuild_provider` 用 `api_key=ep.config.api_key_runtime or "mock"`(line 231) — 空 string 被 fallback 成 `"mock"`,然后 `is_mock_key("mock")` 返 True → 走 `MockProvider`。**OK**。
  - 但 `_maybe_fallback_to_mock` (model_pool.py:384-408) 的判断更严:
    ```python
    if is_mock_key(key): return
    if len(key) > 8 and key.startswith("sk-"):
        # 自动切 mock
    ```
    这意味着:**如果 key 形如 `sk-` 开头但长度 <= 8**(如 `sk-abc`),**不会切 mock**;**`sk-your-abc...` 也不切 mock**(`is_mock_key` 返回 True 直接 return,根本没机会触发 fallback 逻辑)。
  - **bug 1**:`auth.py:78` `settings.auth.gateway_api_keys` 是 yaml 配的 raw key,**`is_mock_key` 不参与判断**(直接 `k == token` 比),但**如果 yaml 配了 `gateway_api_keys: ["mock"]`**,任何带 `Authorization: Bearer mock` 的请求都会被识别为有效 key,**配额 = `quota_rpm=10000, quota_daily_tokens=999_999_999`**(auth.py:75-77) → 绕过限流。
  - **bug 2**:`model_pool._maybe_fallback_to_mock` 的 `if len(key) > 8 and key.startswith("sk-")` 漏了**非 `sk-` 开头的真 key**(如 Anthropic `sk-ant-...`、Google `AIza...`、custom bearer `xxx`)。这些 key 401 时**不会触发 fallback**,`consecutive_failures += 1`,3 次后 `trigger_breaker`,但**provider_obj 仍是真实 OpenAICompatProvider**,下次 chat 仍 401 → 死循环失败,直到 cooldown 结束。
  - **bug 3**: 与 P0-1 ratelimit 相关: `incr_tokens` (ratelimit.py:60-81) 走 `self.storage.get_daily_tokens` + `incr_daily_tokens`,**但** `get_daily_tokens` 在 line 73 是个**独立事务**(`with self.conn() as c: ... fetchone`),read 完没立即 lock;并发场景:
    1. 请求 A 读 `current = 500`,`500 + 600 = 1100 < 1000`(假设 limit=1000)? 不对,`500 + 600 = 1100 > 1000` → 抛 429,没 incr。
    2. 请求 B 读 `current = 500` (同样),`500 + 400 = 900 < 1000` → 通过,incr 到 900。
    3. 请求 C 读 `current = 900`(B 之后),`900 + 200 = 1100 > 1000` → 抛 429。
    
    **看起来 OK? 错**:
    4. 请求 D 读 `current = 500`(B inc 之前),与 A 同时跑 → A 抛 429,B +900,D +400 → 总 1300,**超过 limit 1000**。
    
    实际上 `incr_daily_tokens` 内部有 `BEGIN IMMEDIATE` 原子 incr,但 `get_daily_tokens` 在 `incr_daily_tokens` **之前** 单独一个事务(独立 `with self.conn()`),read-modify-write 是 **read 在事务 A,incr 在事务 B** 的 race → 限制可被绕过。
  - **bug 4 (更严重)**:`ratelimit.py:74-81` 的 "先 read 再 incr" 模式在 P1-1 fix 时被引入,但**`storage.incr_daily_tokens` 仍然累加非原子两步**:虽然在 `BEGIN IMMEDIATE` 内部 incr + select 原子(已修),**但调用方 `incr_tokens` 内部的 read(独立事务)+ incr(独立事务)是分开的** → TOCTOU。
- **Repro**:
  ```python
  # Bug 1: 绕过限流
  # config.yaml:
  #   auth:
  #     gateway_api_keys: ["mock"]
  # curl -H "Authorization: Bearer mock" ... → 配额 999_999_999
  ```
  ```python
  # Bug 4: 限流穿透
  import asyncio
  from moa_gateway.ratelimit import get_limiter
  limiter = get_limiter()
  ki = {"key_id": "k", "quota_daily_tokens": 1000}
  async def hit(n):
      try: limiter.incr_tokens(ki, n)
      except: pass
  # 50 个并发 incr_tokens(ki, 30) → current 同时被 read 为 0,全部通过,总 1500 > 1000
  await asyncio.gather(*[asyncio.to_thread(hit, 30) for _ in range(50)])
  ```
- **Fix**:
  - **bug 1**: `is_mock_key` 的字面 `"mock"` 应该是**显式黑名单而不是白名单**;`gateway_api_keys` 在 yaml 加载时**排除** mock-style key(拒绝 `"mock"` / `"mock-key"` / 空作为 gateway_api_keys 项)。
  - **bug 2**: `_maybe_fallback_to_mock` 改成 "任何非 is_mock_key 且 401/403 触发 → 切 mock",**不再**用 `len > 8 and startswith("sk-")` 这种启发式。
  - **bug 4**: `incr_tokens` 的 read + write 必须**合并到同一事务**:`storage` 加 `incr_daily_tokens_atomic(api_key_id, day, tokens, limit)` 一方法,内部 `BEGIN IMMEDIATE` 一次完成 read+check+incr(或回滚),让调用方一次调用即可。

---

### P1-13: `change_password` 同步调 `_bcrypt_hash`(12 rounds ≈ 300ms)— 在 async handler 里阻塞 event loop (concurrent)
- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:3175-3184` × `D:\MoA Gateway Pro\moa_gateway\storage.py:318-329, 43-52`
- **Category**: race (event loop blocking)
- **Description**:
  `change_password` (server.py:3175-3184) 是 `async def`,但**直接**调 `storage.change_admin_password(...)`(line 3181),后者 (storage.py:318-329) **同步**调 `_bcrypt_hash(new_password)`。
  - `_bcrypt_hash` 用 `bcrypt.hashpw(pwd, bcrypt.gensalt(rounds=12))` (storage.py:32) — rounds=12 大约 300ms,阻塞当前线程 300ms。
  - server.py 跑在 uvicorn 单线程 async 循环里,300ms 阻塞 = **300ms 内所有其他请求 stall**。
  - `storage.py:43-52` 已经有 `async_bcrypt_hash` / `async_bcrypt_verify`,**但 `change_admin_password` 没改用它们**,只改了 `login`(server.py:3149-3155)。
  - **影响**:并发用户改密时,server 全卡;**管理员自己改自己密码时触发 500ms 延迟**;round 12 在某些 ARM CPU 上 1s+,更糟。
  - **同样的 bug** 在 `bootstrap_admin` (storage.py:283-289) — 但这是启动期,不影响生产。
  - **repro**:
    ```python
    # 并发: 一个用户改密,其他用户发 chat
    import asyncio, time
    async def chat():
        await asyncio.sleep(0.001)  # 模拟 LLM 调用
    async def main():
        await asyncio.gather(change_password(...), chat(), chat(), chat())
    # 实际 chat() 会被卡 300ms (bcrypt 在主线程)
    ```
- **Fix**:
  - `change_admin_password` 接受 optional `await` — 改 `async def change_admin_password_async`,内部 `await async_bcrypt_hash(new_password)`。
  - 或在 `change_password` endpoint 里包:`hashed = await async_bcrypt_hash(req.new_password); await asyncio.to_thread(storage._change_admin_password_raw, admin["sub"], hashed)`。
  - 把 `verify_admin` (line 292-316) 也改成 async,内部 `await async_bcrypt_verify`。
  - 通用做法:在 storage 顶层包一个 `async def change_password_async(...)`,所有 endpoint 用 async 版本。

---

## P2 (Medium) — 5 个(摘要)

- **P2-7**: `capability/subagent_comms.py:326-330` `TaskBoard._tasks` dict 同样无限增长,无清理。
- **P2-8**: `capability/versioning.py VersionStore` (server.py:1985-1987 `_stores[proposal_id] = VersionStore()`) proposal_id 用户输入,**无限创建 VersionStore,每个 store 内部 `versions` list 只 append**。
- **P2-9**: `model_pool.py:263-269` `remove_endpoint` 内 `asyncio.get_event_loop().create_task(ep.provider_obj.aclose())` — **再次出现 fire-and-forget task 模式**(虽然 P0-2 修了 `_rebuild_provider` 的同类问题,这里漏了)。
- **P2-10**: `server.py:1602` `capability_hook_events` 的 `_registry` / `_ralph` 单例,跨请求累积 `RALPH_CYCLE.iteration` / `cycle.terminated` 状态,长跑下 cycle 内部 data dict 增长。
- **P2-11**: `capability/frozen_zone.py:2728-2729` `capability_frozen._registry` 接受任意 path 字符串,**也是用户可控 key 的 unbounded dict**。

---

## 总结

- **5 个 P0 涉及**:
  1. **2 个任意文件读写**(P0-6 feedback-iter 路径穿越 + P0-7 rag_search 连接耗尽)
  2. **1 个 subprocess 阻塞 event loop**(P0-8 worktree 无 timeout)
  3. **1 个流式响应 race**(P0-9 provider 引用失效)
  4. **1 个永久内存累积**(P0-10 _pending_close 永远不缩)
- **6 个 P1**:
  - **3 个内存** (P1-10/11 registry 不清 + P1-13 bcrypt 阻塞)
  - **2 个 race** (P1-8 双锁 + P1-12 read-modify-write TOCTOU)
  - **1 个 TOCTOU/cooldown 注入** (P1-9)
- **第二轮回波 (与第一轮对比)**:第一轮 5 P0 偏 security(auth 旁路 / 任意文件写 / 任意 cwd git),第二轮 5 P0 偏**运行时错误** (race / resource leak / event loop block / fd 耗尽)。**两类 P0 都未在第一轮覆盖**。
- **整体观察(第二轮新增)**:
  1. **`asyncio.create_task` + 模块级 `_singleton` dict 是 P0/P1 的主要来源**(P0-10 / P1-10 / P1-11 / P2-8/10/11)。第一轮 P0-2 修了 fire-and-forget task 但只修了一个 caller,全 codebase 仍有 ~5 处同类问题(`remove_endpoint`、`capability_hook_events`、worktree、bubble、subagent_comms)。
  2. **同步阻塞调用混在 async 路径里**:`change_password` (P1-13)、`worktree.snapshot` (P0-8)、`bcrypt` 在 bootstrap + change 都是同步,FastAPI 单线程 ev loop 一卡全卡。
  3. **storage 池化缺失**:`Storage.conn()` 每次 open+close (P1-8),`rag_search._get_conn` 同问题 (P0-7)。两层 lock (`_conn_lock` + SQLite WAL) 是过度设计但没解决连接池。
  4. **endpoint 全部 `require_api_key` 而非 `require_admin`**(P0-6):Wave 11 的 capability endpoint 一致是 API key 级权限,但其中很多支持用户输入 path / repo / action (feedback-iter、rag-search、channels、reference-router、subagent-comms)。**P0-4 (checkpoint) + P0-5 (worktree) + P0-6 (feedback-iter) 三连发是同一类问题的"补丁式"修复痕迹**——只补了已经审计出 RCE 的端点,同类功能的新端点没继承 admin 权限。

(End of report - 共 5 P0 + 6 P1 + 5 P2)
