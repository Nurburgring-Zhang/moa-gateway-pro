# Bug Hunt Report (2026-07-14)

**Target:** `D:\MoA Gateway Pro\` (moa_gateway v1.6.x, Wave 11)
**Scope:** race conditions, resource leaks, security (path traversal, SQL injection, insecure deserialization, auth bypass, SSRF, JWT alg confusion)
**Method:** static review of `storage.py`, `model_pool.py`, `auth.py`, `ratelimit.py`, `server.py`, `capability/checkpoint.py`, `capability/rag_search.py`, `capability/worktree.py`, `providers/{base,openai_compat,__init__}.py`, `prompts.py`

---

## P0 (Critical) — 5 个

### P0-1: `incr_rpm` / `incr_daily_tokens` 不是原子操作 — 限流可被并发穿透 (race)
- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:546-571` + 调用方 `ratelimit.py:49, 70`
- **Category**: race
- **Description**: `incr_rpm` 和 `incr_daily_tokens` 用了两步操作 — 先 `INSERT ... ON CONFLICT DO UPDATE` 增加计数,再另起一个 `SELECT count` 读回。`Storage.conn()` 的 `_conn_lock` 是 Python 层 RLock(进程内),sqlite 默认在多线程间共享一个 connection 时(此处 check_same_thread=False),两个并发事务之间没有上排他锁:
  1. 事务 A:`INSERT ... count = count + 1`(拿到 5)
  2. 事务 B:`INSERT ... count = count + 1`(拿到 6) — 但 A 还没 commit
  3. A 的 `SELECT` 读到 6
  4. B commit,B 的 `SELECT` 读 6
  5. 返回值都丢了 1(典型 lost-update,SQLite WAL 模式下仍可能)
  加上 `incr_daily_tokens` 同样的两步走,使 RPM 计数和日 token 计数**不可信**,攻击者用 N 个并发请求就能把 N×limit 的请求放过去。RPS 高时计数还可能往回走。
- **Repro**:
  ```python
  import asyncio
  from moa_gateway.ratelimit import get_limiter
  from moa_gateway.auth import authenticate_api_key  # 假设一个 rpm=10 的 key
  async def hit():
      get_limiter().check_and_incr({"key_id": "key_test", "quota_rpm": 10,
                                     "quota_daily_tokens": 1000000})
  async def main(): await asyncio.gather(*[hit() for _ in range(200)])
  asyncio.run(main())  # 实际 count 可能只到 130,而非 200
  ```
- **Fix**: 用一个 `UPDATE ... RETURNING count`(SQLite 3.35+)或把整个 inc+select 包在同一个事务里(`BEGIN IMMEDIATE` + `INSERT`/`UPDATE` + `SELECT` + `COMMIT`)。最简的修复是:
  ```python
  c.execute("BEGIN IMMEDIATE")
  c.execute("INSERT ... ON CONFLICT DO UPDATE SET count = count + 1, updated_at = ?",
            (api_key_id, bucket, time.time(), time.time()))
  row = c.execute("SELECT count FROM ratelimit_buckets WHERE ...", ...).fetchone()
  c.execute("COMMIT")
  ```
  同样的修复应用到 `incr_daily_tokens`。

---

### P0-2: `_rebuild_provider` 调 `asyncio.get_event_loop().create_task(aclose())` — sync 函数里 fire-and-forget task + 资源泄漏 (leak + race)
- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:215-220`(以及同模式的 `model_pool.py:258-266`、`218` for `aclose`)
- **Category**: resource leak / race
- **Description**: `_rebuild_provider` 在 `refresh()`(同步,由 FastAPI 同步路径调用)里 `asyncio.get_event_loop().create_task(ep.provider_obj.aclose())`。问题:
  1. `asyncio.get_event_loop()` 在没有 running loop 的线程(同步 FastAPI handler 线程、或 `startup` 之前的 `__init__`)里可能拿到**已关闭的 loop** 或抛 `DeprecationWarning`/`RuntimeError`。
  2. 同步路径里 `get_event_loop().create_task(...)` 会创建 task 但**不 await**,一旦调用栈返回,task 可能被 GC 或从未执行 — provider 的 `httpx.AsyncClient` 永远不关闭 → 底层 TCP 连接(socket)、TLS 上下文、连接池全部泄漏。
  3. `refresh()` 在 `subscribe_settings_change` 回调里也会被调;WebUI 改一次配置,所有 endpoint 的旧 provider 都会"被丢 task"但 task 可能没机会运行。
  4. 即便 loop 存在,`aclose` 任务在 task 调度前,如果新一轮 chat 已经走到 `cur.provider_obj.chat(req)`(`model_pool.py:520`),新 provider 还没建好,`provider_obj` 在 race window 内被替换为 None,会 `AttributeError: 'NoneType' object has no attribute 'chat'`。
- **Repro**:
  ```python
  from moa_gateway.model_pool import get_model_pool
  pool = get_model_pool()                # refresh() 在 __init__ 中调
  # 此刻 fire-and-forget task 在还没起来的 event loop 上创建 → 抛 RuntimeError
  pool.upsert_endpoint({"endpoint_id": "ep1", "provider": "openai", "model": "gpt-4o", "tier": "premium"})
  # 第二次 refresh 又 fire 一个新 task,前一个 task 可能 GC 掉
  pool.refresh()
  ```
- **Fix**:
  - 把 `_rebuild_provider` 改成 `async def`。
  - 维护一个 `self._close_tasks: set[asyncio.Task]` 显式持有 task 引用,在 `stop()` 里 `await asyncio.gather(*self._close_tasks, return_exceptions=True)` 回收。
  - 在没 loop 的情况下,延后关闭(放进队列、由 `start()` 消费),不要在 sync 路径上 spawn task。
  - 同步路径上的关闭改用 `httpx.AsyncClient.close` 的同步兼容(没有,需要重构成一个内部 `_pending_close: list[Provider]` 队列)。

---

### P0-3: `get_or_create_fernet` 的 key 文件存在性 TOCTOU + 写竞态 (race / security)
- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:73-87`
- **Category**: race + security-adjacent
- **Description**: `_get_or_create_fernet()` 走 `if _FERNET_PATH.exists(): read_bytes() else: generate_key() + write_bytes() + chmod`。这个判断在多线程下存在经典 TOCTOU:
  - 线程 A 看到文件不存在 → 生成 key → 写文件
  - 线程 B 同时看到不存在 → 生成**另一个** key → 覆盖写
  - 之前 A 加密的 API key 现在用 B 重新启动后**永久解不开**,且 B 把 A 的 key 覆盖了,**丢密文数据**。
  
  另外 key 文件在 `chmod` 失败时直接 `pass`(line 85-86),在 Windows 上 `os.chmod` 行为本来就有限,导致 `.fernet_key` 可能以继承目录权限保存,本机任何用户都能读到所有 model endpoint 的明文 API key(从 storage 解密得到)。
- **Repro**:
  ```python
  import threading
  from moa_gateway.storage import _get_or_create_fernet
  def hit(): return _get_or_create_fernet()
  fs = [threading.Thread(target=hit) for _ in range(50)]
  for t in fs: t.start()
  for t in fs: t.join()
  # .fernet_key 在 .pyc 缓存里 50 次 generate_key 中可能产生 1 个赢家
  # 但 winners 之前生成的密文全部丢
  ```
- **Fix**:
  - 在 import 时(或单例 lock 里)一次性生成/读 key,而不是每次 `_encrypt`/`_decrypt` 都调一次。
  - 用 `os.open` + `O_CREAT | O_EXCL | O_NOFOLLOW` 原子创建,失败表示别人先创了,直接 `read_bytes`。
  - 启动时用 `os.stat` 检查文件权限,如 mode & 0o077 != 0 直接 raise,而不是 silent pass。

---

### P0-4: `capability_checkpoint` 任意 `root_dir` + `atomic_write` 任意 `path` — 任意文件写 + 目录穿越 (security)
- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:3060-3102` → `D:\MoA Gateway Pro\moa_gateway\capability\checkpoint.py:20-82, 99-156`
- **Category**: security (path traversal + arbitrary file write)
- **Description**: `/v1/capability/checkpoint` 端点只需要 **任意有效的 API key**(不需要 admin),就接受用户完全控制的 `root_dir`、`name`、`payload`、以及 `atomic_write` action 的 `path`/`data`。问题:
  1. `root = body.get("root_dir", "./.moai/checkpoints")` — 用户传 `"C:\\Windows\\System32\\drivers\\etc"` 就往那写。`CheckpointStore.__init__` 调 `os.makedirs(self.root_dir, exist_ok=True)`(line 110-112),**会创建**任意不存在的父目录,只要没被 OS 拦。**`name` 校验在 `_safe_name` 里只挡了 `..` 和 `/\\`**,但 Windows 上 `C:` `\\\\?\\` `NUL` 等保留 device 名仍可写入;Linux 上 `name` 含 `..` 也已被挡。
  2. `action == "atomic_write"` 走的是**裸的 `atomic_write(path, data, encoding=...)`**(server.py:3091-3096),传入任意 `path` + 任意 `data`。`atomic_write` 内部只创建父目录,没做路径边界检查(它是底层 helper,本来就该调),**等于给 API key 用户一份远程文件写原语**。攻击者:
     - 写 `C:\Users\Administrator\.moa-gateway\prompts\aggregator.md` → 下次 MoA 调用就把 prompt 替换成任意内容(后续 LLM 输出被 prompt injection 操控)。
     - 写 `D:\MoA Gateway Pro\.fernet_key`(Windows 下无 .lock 检查)→ 用自己生成的 key 覆盖,fake key 后即用 admin 也能用 API Key,但 model endpoint 的密文就全废了(DOS)。
     - 写 `D:\MoA Gateway Pro\data\moa.db` → 替换数据库。
     - 写 `D:\MoA Gateway Pro\start.py` / `start.bat` → **RCE on next launch**。
  3. `save` action 也类似:`store.save(name, payload)` 中 `payload` 是任意 JSON 可序列化对象,但 `name` 经 `_safe_name` 校验了,只能控 `*.json` 后缀的文件名,**比 atomic_write 危害小**,但仍可污染 `~/.moa-gateway/checkpoints/`。
  
  任何持有合法 API key 的用户(无论是 admin 颁发、yaml 写死,还是 admin JWT)都能利用 — `/v1/capability/checkpoint` 只用了 `require_api_key`,**不是** `require_admin`。
- **Repro**:
  ```bash
  curl -X POST http://127.0.0.1:8000/v1/capability/checkpoint \
    -H "Authorization: Bearer mgw-YOURKEY" \
    -H "Content-Type: application/json" \
    -d '{"action":"atomic_write","path":"D:/MoA Gateway Pro/start.py","data":"# pwned"}'
  # 下次 start.bat / start.py 启动 → RCE
  ```
- **Fix**:
  - 强制改用 `require_admin`(而不是 `require_api_key`)。
  - `root_dir` 强制白名单(如相对 cwd、或 `~/.moa-gateway/checkpoints`)。在 `CheckpointStore` 里用 `Path.resolve()` + `os.path.commonpath` 校验仍在白名单目录内。
  - 直接禁掉 `atomic_write` 走 HTTP 暴露 — 那是给测试用的 helper,不该走 API。
  - 加大小/类型限制,以及把 name 进一步限制到 `[a-zA-Z0-9_-]{1,64}`(目前 `os.path.basename(name) != name` 不挡 `C:` 这种 Windows 盘符)。

---

### P0-5: `capability_worktree` 把 `repo_path` 透传给 `subprocess.run(cwd=...)` — 任意 git 命令在任意目录执行 (security / SSRF-adjacent)
- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:2146-2187` → `D:\MoA Gateway Pro\moa_gateway\capability\worktree.py:34-59`
- **Category**: security
- **Description**: `/v1/capability/worktree` 接收用户控制的 `repo_path` / `repo_path1` / `repo_path2`,直接传给 `subprocess.run(["git", ...], cwd=repo_path, ...)`。任何持有 API key 的用户都能:
  1. `cwd` 设为 `D:\Windows\System32` → 跑 `git rev-parse` 等于在 Windows 目录跑 git 命令。git 会在 cwd 找 `.git/` 目录,不在时会沿父目录上溯,**这本身不会 RCE**,但 `git config --get-regexp` 等命令支持 `include.path`,攻击者可以间接读到 `C:\Users\Administrator\.gitconfig`(信息泄露)。
  2. `cwd` 设为 `C:\` → `WorktreeManager.list_worktrees` 会去 `C:\.git` 找,这个路径 server 通常没权限,但能拿 error 信息推断(有限但仍然信息泄露)。
  3. 更阴险的是:`worktree` 端点本身是 `require_api_key`,**不是 admin**。一旦配合 `repo_path = "C:\\Program Files\\Git"` 或类似带空格/特殊字符的路径,`subprocess.run(..., cwd=...)` 在 Windows 上会调用 `CreateProcess`,**对 cwd 的可执行性没有限制**;虽然不会执行命令,但 `git` 在 cwd 不可写时会失败,可能触发 `GitCommandError` 含 cwd 路径。
  4. `repo_path1` / `repo_path2` 走 `diff_snapshots` 路径,`snapshot` 内部跑 `git rev-parse` / `git ls-files` / `git status --porcelain` — 这些命令**自己会通过 `.gitconfig` 加载可执行 hook**(`core.fsmonitor` 等),攻击者可以预先把 `.gitconfig` 放在一个已知 path(如他自己的 home),让 server 端 git 在 cwd 解析到他控制的 config。这需要 `HOME` 环境变量能被攻击者影响(通常不能),但 `GIT_CONFIG_GLOBAL`、`GIT_CONFIG_SYSTEM` 等环境变量如果 server 端没 unset,攻击者就能利用。
  
  本质:**给低权限 API key 用户**访问 git 内部命令 + 任意 cwd,违反最小权限。
- **Repro**:
  ```bash
  curl -X POST http://127.0.0.1:8000/v1/capability/worktree \
    -H "Authorization: Bearer mgw-YOURKEY" -H "Content-Type: application/json" \
    -d '{"action":"snapshot","repo_path":"C:\\Windows\\System32"}'
  # 返回 git 错误信息,可能含 cwd 路径 + Windows 版本信息
  ```
- **Fix**:
  - 改用 `require_admin`。
  - 在 `capability_worktree` 里加白名单:只允许 `repo_path` 在 server 根目录或 `~/.moa-gateway/` 下。
  - 启动时 `os.environ.pop("GIT_CONFIG_GLOBAL", None); os.environ.pop("GIT_CONFIG_NOSYSTEM", None)` 之类 hygiene。
  - 在 `subprocess.run` 里加 `env={}` 显式 env。

---

## P1 (High) — 7 个

### P1-1: `incr_daily_tokens` 在超额后才抛 429 — 但 counter 已被累加,后续会被永久欠费 (race / business logic)
- **File**: `D:\MoA Gateway Pro\moa_gateway\ratelimit.py:60-75`
- **Category**: race + leak
- **Description**: `incr_tokens` 先 `incr_daily_tokens` 累加,再判断 `cur > daily_limit`,超了抛 429。但 `incr_daily_tokens` 已经把超过 limit 的部分计入,以后每次请求都会在 `cur` 基础上继续累加,永远超 limit,用户被**永久锁死**直到下一天。而且因为 `incr_daily_tokens` 是 read-modify-write(参见 P0-1),超额判断也是不准确的。
- **Repro**:
  ```python
  # quota_daily_tokens=1000,先发 2000 tokens,后续 1 token 的请求都 429
  for _ in range(100):
      get_limiter().incr_tokens({"key_id":"k","quota_daily_tokens":1000}, 20)
  # 然后: limiter.incr_tokens(k, 1) → 429,counter 已经 2001 永远 > 1000
  ```
- **Fix**: 在 `incr_daily_tokens` 里 atomic 地做"if current + tokens > limit then don't increment + return -1",让调用方能区分"加了"和"没加"。

---

### P1-2: `pool._check_all_health` 超时分支里 `isinstance(getattr(ep.provider_obj, '__class__', None), type(None))` 是死代码 (bug / code quality)
- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:318-332`
- **Category**: race / resource leak(隐性)
- **Description**: `model_pool.py:322` 写 `if (ep.config.enabled and ep.config.api_key_runtime
    and not isinstance(getattr(ep.provider_obj, '__class__', None), type(None)) and ...)`,`getattr(ep.provider_obj, '__class__', None)` 当 `provider_obj` 是 None 时返回 `None`,`isinstance(None, type(None))` 永远为 `True`,取 `not` 后为 `False`,**整个条件恒为 False**。意图应该是"provider_obj 不是 None 且不是 MockProvider 才考虑 fallback",但实际**永远不会触发"切 mock"逻辑**。`startup health check timeout → auto-fallback to MockProvider` 这段是死代码,行为是:启动卡超时 → 这些端点保持 `health_status="unknown"` 30 秒,影响 dashboard 显示。
- **Repro**: 让所有 endpoint 的 `health_check` 都 sleep 5s(超过 3s timeout)→ 启动后 dashboard 看到的应是 mock 模式,实际是 unknown。
- **Fix**: 改成
  ```python
  if (ep.config.enabled and ep.config.api_key_runtime
      and ep.provider_obj is not None
      and ep.provider_obj.__class__.__name__ != "MockProvider"):
  ```
  顺便补上 race 修复(超时分支里可能改 `provider_obj`,但 `_rebuild_provider` 不是 async,不安全)。

---

### P1-3: `_build_provider` 把 `api_key_runtime` 通过 `model_pool.py:227` 拼成 `"mock"` — 触发了 `auth.py` 的 `decode_jwt_token` 路径当 key 以 `eyJ` 开头时 (logic)
- **File**: `D:\MoA Gateway Pro\moa_gateway\model_pool.py:224-230` × `D:\MoA Gateway Pro\moa_gateway\auth.py:42-47`
- **Category**: security(auth bypass 边缘)
- **Description**: 任何形如 JWT(`token.count(".") == 2`)且 header 以 `eyJ` 开头的 token,`authenticate_api_key` 走 `decode_jwt_token` 路径,只有当 `info["role"] == "admin"` 才返回。这条**没有检查签名**就放 admin role 是**正确**的(因为 HS256 验签在 `decode_jwt_token` 里)— 但下面紧跟着的 `storage.find_api_key(token)` 也会被尝试,如果 admin 之前手贱在 `api_keys` 表里插入了一个以 `eyJ.eyJ.sig` 形式的 key(虽然不可能正常生成),就会发生奇怪覆盖。
  真正的问题在 model_pool.py:227:`api_key=ep.config.api_key_runtime or "mock"` 把 `"mock"` 字面量当 api_key 传给 provider 的 `__init__`,但 `is_mock_key`(`providers/__init__.py:28-39`)先检查 `is_mock_key(api_key)`,正好 `"mock"` 命中第 37 行 `k == "mock" or k == "mock-key"`。所以 OK。**但是**当 `ep.config.api_key_runtime` 是空字符串(切 mock 后,`ep.config.api_key_runtime = ""` in `_maybe_fallback_to_mock`),line 227 走 `... or "mock"`,触发 `is_mock_key("mock")` 切 MockProvider。**这路径是正确的**。此项降级,不算严重。
  
  真正该上报的是 P0-2(同步 path 的 fire-and-forget task)和 P0-4(任意文件写)。但发现一个相关小问题:`auth.py:42` 用 `count(".") == 2` 判断 JWT,这是脆弱的(任何含两个点的字符串都过),应该用 regex 或 jose 的 try-decode 探测。
- **Repro**: 提交 `Authorization: Bearer not.a.jwt` → 进 `decode_jwt_token` → 验签失败 → return None → 继续走 storage/yaml。但因 `count(".") == 2` 进了 try 路径,**不是 bug**,只是低效。
- **Fix**: 改用 try/except + `jwt.get_unverified_header` 探测,或直接用 `re.fullmatch(r"eyJ[A-Za-z0-9_=\-]+\.eyJ[A-Za-z0-9_=\-]+\.[A-Za-z0-9_\-]+", token)`,再决定走哪条路径。

---

### P1-4: `verify_admin` 里有"默认密码检测"被 plaintext-equal 比较 (security)
- **File**: `D:\MoA Gateway Pro\moa_gateway\storage.py:267-272`
- **Category**: security
- **Description**: `verify_admin` 在用户成功登录后,`must_change = (username == settings.auth.admin_username and password == settings.auth.admin_password ...)` — **用明文比较密码**确认是不是默认密码。
  1. 这要求 `settings.auth.admin_password` 在内存里以明文保存(看 `config.py` 即可确认 — 是 Pydantic BaseModel,默认就是明文 str)。
  2. 如果 process dump / 错误日志 / debug 异常 traceback 把 password 写出来,就被泄露。
  3. 实际上更严重的:`server.py:3126-3129` 在 `login` 路径里也做了同样的 plaintext 比较,而且**这两个地方每次都读取 settings.auth.admin_password(明文)**,给到 `verify_admin` 和 `login` 后会做一次 `_bcrypt_verify` 再 `==` 明文,意味着**明文密码在内存中存在两次以上**。
- **Repro**: 触发一个 unhandled exception 在 verify_admin 之后 → 异常 traceback 里能带出 settings(如果 logger 打 frame locals)。
- **Fix**: 登录时用 `bcrypt.verify` 通过即可,**不在代码里再比较 plaintext**。在 `bootstrap_admin` 时,把"原始明文"用 `bcrypt` 算一次 hash 存到 settings 内存,登录时用 `bcrypt.verify(password, settings.auth.admin_password_hash) == True` 判定是否默认密码。或更彻底:让 WebUI 永远强制改密,跟"是否是默认密码"解耦。

---

### P1-5: `webui_assets` 路径校验在 Windows 上可被 NTFS 8.3 short name 绕过 (security / path traversal)
- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:3282-3297`
- **Category**: security (path traversal)
- **Description**: 
  ```python
  if "/" in name or "\\" in name or ".." in name or name.startswith("."):
      raise HTTPException(404, "not found")
  p = WEBUI_DIR / name
  try:
      resolved = p.resolve()
      if not str(resolved).startswith(str(WEBUI_DIR.resolve())):
          raise HTTPException(404, "not found")
  ```
  Windows NTFS 上**8.3 短文件名**(`PROGRA~1` 之类)允许 `name` 包含 `~` 和数字且仍指向 WEBUI_DIR 之外;如果 server 安装路径下有 `PROGRA~1` 指向 `C:\Program Files`(可能),`p = WEBUI_DIR / "..\\..\\..\\PROGRA~1"` 仍会被 `..` 检查挡住(因为 name 不允许 `..`),但**`name` 本身可以是 `WEBUI~1`**这种同目录的短名。
  更实际的问题:`p.resolve()` 在 Windows 上对**符号链接/reparse point**不防 — `WEBUI_DIR` 内的某个子目录如果被 symlink 指向外部,`resolved` 仍可能在 WEBUI_DIR 内,但 `FileResponse` 会送出去。需要在 `os.path.commonpath` 校验,而不是 `startswith`(`startswith` 在父目录是 `/foo/bar`、解析后是 `/foo/bar2` 时会误判)。
- **Repro**: (依赖文件系统) 在 Windows 上创建 `WEBUI_DIR/leak.txt → 指向 C:\Windows\win.ini` 的 symlink,`GET /webui/leak.txt` → 返回 `win.ini`。
- **Fix**:
  ```python
  base = WEBUI_DIR.resolve()
  resolved = (base / name).resolve()
  if os.path.commonpath([str(resolved), str(base)]) != str(base):
      raise HTTPException(404, "not found")
  ```
  且禁掉跟随 symlink(`stat.S_ISLNK(os.lstat(p).st_mode)` 检查)。

---

### P1-6: `auth.py` 的 `_bearer_or_raw` 把 `bearer <token>` 后的 token trim,但 `Authorization` 头里多个 token 用逗号分隔时仍只拿第一个 (security / parsing)
- **File**: `D:\MoA Gateway Pro\moa_gateway\auth.py:21-24`
- **Category**: security
- **Description**: 
  ```python
  def _bearer_or_raw(token: str) -> str:
      if token.lower().startswith("bearer "):
          return token[7:].strip()
      return token.strip()
  ```
  HTTP RFC 7230 允许多个 `Authorization` 头(实际少见,proxy 可能加),FastAPI 把同名的 header 合并为逗号分隔字符串。若一个反代注入 `Authorization: Bearer attacker-key, Bearer victim-key`,`token.lower().startswith("bearer ")` True,`token[7:]` = `"attacker-key, Bearer victim-key".strip()`。后续 `find_api_key` 拿整串查 → 找不到 → 返 401。这种用法在 `WWW-Authenticate: Bearer` 流的客户端**不会重试**,用户体验差。
  
  真正的隐患更简单:`_bearer_or_raw` 没限制 token 长度,理论上一个 10MB 的 `Authorization` 头能让 server 内存飙升(虽然 `Request.headers` 本身有 size cap,但不是 application 层责任)。
- **Fix**: 限制 token 长度(如 `<= 256`),并在逗号分隔时取第一个之后做更严格的 sanity check(用 `re.match(r"^[\w\-\.]+$", token)`,或用 `secrets.compare_digest` 风格的等长比较)。

---

### P1-7: `chat_completions` 把 `req.model` 直接当 endpoint_id 查 `pool.endpoints` — 不存在时静默回退到 router,但 router 又会查 `pool.endpoints`,可能死循环 (logic)
- **File**: `D:\MoA Gateway Pro\moa_gateway\server.py:327-332`
- **Category**: resource leak(若 router 也降级,可能无限递归或重复调用)
- **Description**:
  ```python
  if is_auto or model_id not in pool.endpoints:
      router = get_router()
      decision = router.route(messages[-1].get("content", ""))
      if not decision.primary:
          raise HTTPException(503, "no available model")
      model_id = decision.primary.id
  ```
  `router.route(...)` 返回的 `decision.primary.id` 是个 endpoint id 字符串,**没有校验**这个 id 是否仍在 `pool.endpoints` 里(可能在两次调用之间 `remove_endpoint` 删了)。然后 `pool.call(model_id, ...)` 会 `raise ValueError(f"endpoint {model_id} not found")` → 包成 `502 model call failed: ...`。对用户暴露"endpoint not found"细节。
  更微妙的:`model_id` 可能是 `"auto"`,如果用户传 `model="auto"`,进入 if 分支后 router 给了个 id,如果 router 内部又对 "auto" 做特殊判断(取决于实现),可能进入死循环或返回的 id 还是 "auto"。
- **Repro**:
  ```bash
  # 假设删了某个 endpoint
  curl -X POST http://127.0.0.1:8000/v1/chat/completions \
    -H "Authorization: Bearer mgw-..." -d '{"model":"deepseek-v3",...}'
  # 现在 router 还以为这个 endpoint 在(因为 router 在启动时 snapshot),返回 decision.primary.id = "deepseek-v3"
  # pool.call(deepseek-v3) → ValueError
  ```
- **Fix**: router 的 snapshot 应通过 `pool.snapshot()` 而不是缓存,或者在 server 这边在用 `decision.primary.id` 之前再 `if model_id not in pool.endpoints: raise HTTPException(503)`。

---

## P2 (Medium) — 6 个(摘要)

- **P2-1**: `storage.py:54-65` 重复定义了 `_bcrypt_hash` 和 `_bcrypt_verify`(同 28-39 行),Python 取后一个,前一个 dead code。删掉重复定义,避免后人混淆(若有人改了前一个,后一个仍生效 → 静默 bug)。
- **P2-2**: `ratelimit.py:14-16` 的 `_bucket_id` 用 `int(time.time()) // 60`,在 epoch 边界(每分钟第 60 秒)所有 key 同时换 bucket,可能造成限流突刺。改用 `int(time.time() * 1000) // 60_000` 仍不能完全解决(同一 ms 内所有 key 同步切),可考虑加 per-key jitter。
- **P2-3**: `model_pool.py:215-220` 没有处理 `provider_obj` 已 `aclose` 但 task 还没跑完的窗口期(参见 P0-2),下个 `pool.call` 拿到一个已关闭的 client。
- **P2-4**: `capability\checkpoint.py:20-82` 的 `atomic_write` 在 Windows 上 `os.replace` 跨盘失败时会 `OSError`(跨盘 mv 不原子),但代码 `try/except` 兜底后只是清 tmp,目标文件状态未知 — 跨盘场景下需要 fallback 到 `shutil.move` 显式说明非原子。
- **P2-5**: `capability\rag_search.py:71-82` 的 `_get_conn` 用 `isolation_level=None`(autocommit),但上层 `_cache_get`/`_cache_put` 显式 `conn.close()`,锁 `_db_lock` 进程内串行化。如果 server 多 worker(`uvicorn --workers 4`),不同 worker 进程**不共享 `_db_lock`**,SQLite WAL 在多 writer 下会触发 `SQLITE_BUSY`;虽然 `timeout=5.0` 兜底,但 5s 慢请求可能积压。建议在 `pragma` 里加 `journal_mode=WAL`(没设)。
- **P2-6**: `server.py:184-204` 的 `add_security_headers` middleware 限制 1MB body,但 `Content-Length` 是 header 而 `request.body()` 在 FastAPI 里是 stream,实际 body 大小可能由攻击者通过 chunked transfer 绕过 — 校验 `Content-Length` 后还得在 stream 读取时二次累加。

---

## 总结

- **5 个 P0** 里有 3 个 security、1 个 race、1 个 race+leak 混合。P0-4(任意文件写)和 P0-5(任意 cwd git 命令)是**最容易在漏洞悬赏/渗透测试里被发现**的;P0-1 / P0-3 是经典的并发问题,出现在多 worker / 频繁配置变更 / 启动时 race 场景。
- **7 个 P1** 主要是 quality + defense-in-depth。建议 P1-1 和 P1-4 优先修(都是 user-facing 影响:永久锁死 / 明文密码在内存中)。
- **1 个 P0 修复后会暴露的次生 bug**:`pool._lock = asyncio.Lock()`(`model_pool.py:138`)**整个生命周期内从未被 acquire** — `refresh` / `_rebuild_provider` / `upsert_endpoint` / `remove_endpoint` 都是无锁的,即便修了 P0-2,`pool.endpoints` 字典的并发修改仍不安全。`require_admin` 端点虽然单线程串行化,但 `subscribe_settings_change` 回调和 `pool.upsert_endpoint` 可以同时触发。需要加 `with self._lock:` 包所有 mutator。
- **整体观察**:`/v1/capability/*` 端点全部用 `require_api_key`(不是 admin),而这些端点里**很多允许 file path / git cwd / subprocess**(见 P0-4 / P0-5),这是 Wave 11 新增能力的安全债,审计/权限分层没跟上功能扩展。Wave 12 应该把这些"执行/写文件"类能力移到 `require_admin` 之下,或者按 admin role 做 ABAC。
