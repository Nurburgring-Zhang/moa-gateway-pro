# MoA Gateway Pro 长任务模式改造方案

> **目标**: 在保留 v1.8.1 全部能力(MoA 编排 / 122 端点 / 11 services / 91 OpenAPI schemas)的基础上,加一层"长任务模式",让 100+ 小时超长项目也能用同一套部署。
>
> **核心思路**: 最小侵入,新能力走"可选启用"路径,旧代码 0 改动。

---

## 0. 设计原则

1. **向后兼容**: 现有 91 个 Pydantic schemas + 122 个端点 0 改动,新功能走 opt-in
2. **复用现有层**: 走 `Storage` 单例(SQLite WAL) + `ServiceBase` 架构 + `_ModelBase` Pydantic 模式
3. **两条路径并存**:
   - **路径 A**(默认,保持原样): 单次请求,无状态,高 RPS
   - **路径 B**(opt-in,带 `thread_id`): 长任务,SQLite 缓存,跨请求记忆
4. **Skill 走 JSON 不走 Markdown**: 跟 MoA 现有 Pydantic capability registry 兼容,机器可读

---

## 1. 架构总览(4 层)

```
┌─────────────────────────────────────────────────────────────┐
│  HTTP 层  /v1/chat/completions (带 thread_id 透传)         │
│           /v1/threads  (新建/查询/删除)                     │
│           /v1/skills  (CRUD)                                │
├─────────────────────────────────────────────────────────────┤
│  Service 层  long_task_service.py (新)                     │
│             ├─ get_thread(thread_id) → messages[]          │
│             ├─ append_message(thread_id, role, content)    │
│             ├─ match_skills(query) → skill refs            │
│             └─ record_skill_usage(skill_id, ok)            │
├─────────────────────────────────────────────────────────────┤
│  Storage 层  storage.py 单例 + 3 张新表                   │
│              thread_sessions / thread_messages /           │
│              thread_skills                                  │
├─────────────────────────────────────────────────────────────┤
│  Capability 层  skills/ 目录 (git 跟踪)                    │
│                  ├─ deploy-check/SKILL.json                 │
│                  ├─ deploy-check/references/xxx.md          │
│                  └─ ... (跟 capability/ 解耦,独立目录)    │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 4 个核心组件设计

### 2.1 thread_id 注入(2 行 Pydantic + 6 行 server.py)

**Pydantic 模型加字段** (`req_models.py` 不动,在 `server.py` 内联 `ChatCompletionRequest` 上加一行):

```python
class ChatCompletionRequest(BaseModel):
    # ... 现有 16 个字段不动 ...
    thread_id: Optional[str] = Field(default=None, max_length=128,
                                     description="可选:长任务线程 ID,带了就走短时记忆模式")
```

**server.py `chat_completions` 端点加 6 行** (在 `messages = [m.model_dump(...)]` 之后):

```python
# 长任务模式 opt-in
thread_ctx = None
if req.thread_id:
    from .services.long_task_service import get_long_task_service
    thread_ctx = get_long_task_service()
    history = thread_ctx.get_recent_messages(req.thread_id, limit=20)
    messages = history + messages  # 短时记忆注入
    # 匹配 Skill
    skill_hints = thread_ctx.match_skills(messages[-1].get("content", ""), top_k=3)
    if skill_hints:
        # 把 Skill 摘要塞进 system prompt,user 看不到
        messages.insert(0, {"role": "system", "content": skill_hints})
```

**收尾 5 行** (在 `_log_request` 之后):

```python
# 长任务模式: 写回这一轮
if thread_ctx and req.thread_id:
    thread_ctx.append_message(req.thread_id, "user", req.messages[-1].content or "")
    thread_ctx.append_message(req.thread_id, "assistant", resp.content or "")
```

**总侵入量: 13 行 server.py 改动, 1 行 Pydantic 字段。0 现有代码路径受影响(没 thread_id 走老路)。**

### 2.2 SQLite schema(3 张新表,加在 `storage.py` 的 SCHEMA 字符串末尾)

```sql
-- 1) 线程元数据(谁开的、过不过期、用的什么 model)
CREATE TABLE IF NOT EXISTS thread_sessions (
    thread_id TEXT PRIMARY KEY,
    api_key_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,        -- 默认 7 天后过期
    message_count INTEGER DEFAULT 0,
    primary_model TEXT,
    total_tokens INTEGER DEFAULT 0,
    metadata TEXT                    -- JSON: tags / project / user_label
);
CREATE INDEX IF NOT EXISTS idx_thread_sessions_api_key
    ON thread_sessions(api_key_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_thread_sessions_expires
    ON thread_sessions(expires_at);   -- 后台清理

-- 2) 消息历史(短时记忆的源)
CREATE TABLE IF NOT EXISTS thread_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- user/assistant/system/tool
    content TEXT,                    -- 允许 NULL(纯 tool_call)
    tool_calls TEXT,                 -- JSON, 可空
    tool_call_id TEXT,               -- 可空
    timestamp REAL NOT NULL,
    tokens_estimate INTEGER,         -- 写入时估的 token 数
    FOREIGN KEY (thread_id) REFERENCES thread_sessions(thread_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_thread_messages_thread_ts
    ON thread_messages(thread_id, timestamp DESC);

-- 3) Skill 注册表(跟我们自己的 skills/ 目录同步,带版本/审核状态)
CREATE TABLE IF NOT EXISTS thread_skills (
    skill_id TEXT PRIMARY KEY,        -- e.g. "deploy-check"
    version TEXT NOT NULL,            -- semver
    title TEXT NOT NULL,
    description TEXT,
    trigger_patterns TEXT,            -- JSON array
    content_path TEXT,                -- skills/<id>/SKILL.json 相对路径
    references_path TEXT,             -- skills/<id>/references/ 目录
    enabled INTEGER DEFAULT 1,
    approved_by TEXT,                 -- 谁审的(admin user id)
    approved_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    usage_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_thread_skills_enabled
    ON thread_skills(enabled);
```

**特点**:
- 跟现有 `request_logs` 同一 SQLite 文件 (`DATA_DIR/state.db`),WAL 模式 + busy_timeout 已就绪
- `thread_sessions.expires_at` 配后台清理任务(每天跑一次,删过期)
- `ON DELETE CASCADE` 删除 thread 自动清消息
- `usage_count` / `success_count` 自动统计(给 Skill 排序用)

### 2.3 `services/long_task_service.py` (新 service,跟其他 11 个并列)

```python
"""services/long_task_service.py — 长任务模式 service

提供:
- thread 生命周期(create / get / append / expire)
- 短时记忆读写(最近 N 轮)
- Skill 匹配(简单关键词 + tag 匹配,Phase 2 升级向量检索)
"""
from __future__ import annotations
import time
import json
import uuid
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

from .base import ServiceBase, ServiceMethod, ServiceResult, ServiceRegistry
from ..storage import get_storage


SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"  # 仓库根/skills/

class LongTaskService(ServiceBase):
    name = "long_task"

    def __init__(self):
        super().__init__()
        self.storage = get_storage()

    @ServiceMethod(description="创建新线程")
    def create_thread(self, api_key_id: str, metadata: Optional[Dict] = None,
                      ttl_days: int = 7) -> ServiceResult:
        thread_id = "thr-" + uuid.uuid4().hex[:16]
        now = time.time()
        with self.storage.conn() as c:
            c.execute(
                "INSERT INTO thread_sessions(thread_id, api_key_id, created_at, "
                "updated_at, expires_at, metadata) VALUES(?,?,?,?,?,?)",
                (thread_id, api_key_id, now, now, now + ttl_days * 86400,
                 json.dumps(metadata or {}))
            )
        return ServiceResult(ok=True, data={"thread_id": thread_id, "expires_at": now + ttl_days * 86400})

    @ServiceMethod(description="读最近 N 条消息(默认 20)")
    def get_recent_messages(self, thread_id: str, limit: int = 20) -> ServiceResult:
        with self.storage.conn() as c:
            rows = c.execute(
                "SELECT role, content, tool_calls, tool_call_id, timestamp, tokens_estimate "
                "FROM thread_messages WHERE thread_id=? ORDER BY timestamp DESC LIMIT ?",
                (thread_id, limit)
            ).fetchall()
        msgs = [
            {"role": r["role"], "content": r["content"],
             "tool_calls": json.loads(r["tool_calls"]) if r["tool_calls"] else None,
             "tool_call_id": r["tool_call_id"]}
            for r in reversed(rows)   # 时间正序
        ]
        return ServiceResult(ok=True, data={"messages": msgs, "count": len(msgs)})

    @ServiceMethod(description="追加一条消息")
    def append_message(self, thread_id: str, role: str, content: Optional[str] = None,
                       tool_calls: Optional[List[Dict]] = None,
                       tool_call_id: Optional[str] = None,
                       tokens_estimate: int = 0) -> ServiceResult:
        now = time.time()
        with self.storage.conn() as c:
            c.execute(
                "INSERT INTO thread_messages(thread_id, role, content, tool_calls, "
                "tool_call_id, timestamp, tokens_estimate) VALUES(?,?,?,?,?,?,?)",
                (thread_id, role, content, json.dumps(tool_calls) if tool_calls else None,
                 tool_call_id, now, tokens_estimate)
            )
            c.execute(
                "UPDATE thread_sessions SET updated_at=?, message_count=message_count+1, "
                "total_tokens=total_tokens+? WHERE thread_id=?",
                (now, tokens_estimate, thread_id)
            )
        return ServiceResult(ok=True, data={"thread_id": thread_id})

    @ServiceMethod(description="匹配相关 Skill(简单关键词,Phase 2 升级向量)")
    def match_skills(self, query: str, top_k: int = 3) -> ServiceResult:
        # 简单实现: skill title/description/trigger_patterns 关键词命中
        keywords = [w.lower() for w in query.split() if len(w) >= 3][:20]
        if not keywords:
            return ServiceResult(ok=True, data={"skills": []})
        with self.storage.conn() as c:
            rows = c.execute(
                "SELECT skill_id, title, description, trigger_patterns FROM thread_skills "
                "WHERE enabled=1 ORDER BY usage_count DESC"
            ).fetchall()
        hits = []
        for r in rows:
            text = (r["title"] + " " + (r["description"] or "")).lower()
            patterns = json.loads(r["trigger_patterns"] or "[]")
            score = sum(1 for k in keywords if k in text)
            score += sum(2 for p in patterns if any(k in p.lower() for k in keywords))
            if score > 0:
                hits.append({"skill_id": r["skill_id"], "title": r["title"],
                             "description": r["description"], "score": score})
        hits.sort(key=lambda x: x["score"], reverse=True)
        return ServiceResult(ok=True, data={"skills": hits[:top_k]})

    @ServiceMethod(description="注册/更新 Skill(从 skills/<id>/SKILL.json 同步)")
    def register_skill(self, skill_id: str, version: str, title: str,
                       description: str, trigger_patterns: List[str],
                       content_path: str, references_path: str = "",
                       approved_by: str = "") -> ServiceResult:
        now = time.time()
        with self.storage.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO thread_skills(skill_id, version, title, description, "
                "trigger_patterns, content_path, references_path, approved_by, approved_at, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,COALESCE("
                "(SELECT created_at FROM thread_skills WHERE skill_id=?),?),?)",
                (skill_id, version, title, description, json.dumps(trigger_patterns),
                 content_path, references_path, approved_by, now, skill_id, now, now)
            )
        return ServiceResult(ok=True, data={"skill_id": skill_id})

    @ServiceMethod(description="加载 Skill 内容(按需注入)")
    def load_skill(self, skill_id: str) -> ServiceResult:
        # 安全扫描(SQL 注入/密钥外传/dangerous shell)
        from .safety_service import get_safety_service
        safety = get_safety_service()
        with self.storage.conn() as c:
            row = c.execute(
                "SELECT content_path, references_path FROM thread_skills WHERE skill_id=? AND enabled=1",
                (skill_id,)
            ).fetchone()
        if not row:
            return ServiceResult(ok=False, error="skill not found")
        # 读 SKILL.json
        path = Path(row["content_path"])
        if not path.is_absolute():
            path = SKILLS_DIR.parent / path   # 相对仓库根
        scan = safety.scan_skill_file(path)   # 调现有 safety_service
        if not scan.ok:
            return ServiceResult(ok=False, error=f"safety: {scan.reason}")
        content = path.read_text(encoding="utf-8")
        # 更新 usage
        with self.storage.conn() as c:
            c.execute("UPDATE thread_skills SET usage_count=usage_count+1 WHERE skill_id=?", (skill_id,))
        return ServiceResult(ok=True, data={"skill_id": skill_id, "content": content})

    @ServiceMethod(description="清理过期线程")
    def cleanup_expired(self) -> ServiceResult:
        now = time.time()
        with self.storage.conn() as c:
            count = c.execute(
                "DELETE FROM thread_sessions WHERE expires_at<?", (now,)
            ).rowcount
        return ServiceResult(ok=True, data={"deleted": count})


# 注册到全局 registry
LongTaskService().register(ServiceRegistry)
```

**接入方式**: server.py `lifespan` 加 2 行(跟现有 11 个 service 一样):

```python
from .services.long_task_service import LongTaskService
# 已有 11 个 services: agent_service / capability_dispatcher / config_service / ...
LongTaskService().register(ServiceRegistry)
```

### 2.4 SKILL.json 格式(放仓库 `skills/<id>/SKILL.json`)

```json
{
  "$schema": "https://moa-gateway-pro.io/schemas/skill-v1.json",
  "skill_id": "deploy-check",
  "version": "1.0.0",
  "title": "检查服务部署状态",
  "description": "巡检一个 endpoint 是否在跑,健康端点是否 200,返回结构化摘要",
  "trigger_patterns": [
    "部署状态", "服务检查", "health check", "deploy check"
  ],
  "tags": ["devops", "monitoring"],
  "category": "operations",
  "input_schema": {
    "endpoint_id": "string (必填,模型端点 ID)"
  },
  "output_schema": {
    "ok": "boolean",
    "checks": "array<{name, ok, detail}>",
    "summary": "string"
  },
  "procedure": [
    "1. POST /v1/endpoints/health-check {endpoint_id}",
    "2. 检查返回的 checks 数组",
    "3. 输出 ok=true/false + 失败项 detail"
  ],
  "pitfalls": [
    "endpoint_id 区分大小写",
    "日志可能被轮转,只查最近 24h",
    "健康端点不是 /health 而是 /v1/endpoints/{id}/ping"
  ],
  "verification": "服务 active, 端点 200, 无 ERROR 日志",
  "references": [
    {"name": "endpoint-spec", "path": "references/endpoint-spec.md"},
    {"name": "common-errors", "path": "references/common-errors.md"}
  ],
  "approved_by": "admin",
  "approved_at": "2026-07-19T00:00:00Z"
}
```

**目录结构**:

```
skills/                                  # 仓库根,git 跟踪
├── deploy-check/
│   ├── SKILL.json                       # 主入口(必填)
│   ├── references/                      # 按需加载的详细文档
│   │   ├── endpoint-spec.md
│   │   └── common-errors.md
│   └── examples/                        # (可选)调用样例
│       └── sample.sh
├── flask-k8s-deploy/                    # 跟 Hermes 的同名 Skill 对应
│   ├── SKILL.json
│   └── references/
│       └── ...
└── README.md                            # 解释本目录怎么用
```

**关键设计**:
- **JSON 不用 Markdown**: 机器可读,直接 Pydantic 校验
- **trigger_patterns**: 关键词匹配,Phase 2 接向量检索(sentence-transformers)
- **input_schema / output_schema**: 跟现有 capability 的 Pydantic schema 同构
- **pitfalls**: 显式列已知坑,比 Hermes "Agent 自己写"更可控
- **approved_by / approved_at**: 强制审核流,Skill 进生产前必须 admin 签字
- **references/**: 渐进加载,只摘要进 system prompt,详细文档按需 `skill_view(name, path)`

---

## 3. server.py 改动总览(diff 级别)

| 位置 | 改动量 | 风险 |
|---|---|---|
| `ChatCompletionRequest` 加 `thread_id` 字段 | 1 行 | 0(向后兼容) |
| `chat_completions` 端点头部加 thread_ctx 注入 | 6 行 | 0(只在 thread_id 不空时生效) |
| `chat_completions` 端点尾部加回写 | 5 行 | 0(同上) |
| `_stream_single` 流式分支(可选,Phase 2) | 8 行 | 低(流式 + 短时记忆要小心) |
| `lifespan` 注册 `LongTaskService` | 2 行 | 0(跟现有 11 service 同流程) |
| **新增 `/v1/threads/*` 端点(CRUD)** | 60 行 | 中(新端点) |
| **新增 `/v1/skills/*` 端点(CRUD + load)** | 80 行 | 中(新端点) |

**总计**:
- 现有路径改动: 14 行
- 新增端点: 4 个 (`POST /v1/threads`, `GET /v1/threads/{id}`, `DELETE /v1/threads/{id}`, `GET /v1/threads/{id}/messages`, `GET /v1/skills`, `POST /v1/skills`, `GET /v1/skills/{id}/load`)
- 现有 122 端点 0 改动
- 现有 91 Pydantic schemas 0 改动(只增 1 个字段)

---

## 4. 跟现有 11 services / 91 OpenAPI schemas 兼容

### 4.1 11 services 怎么用 long_task

**方式 A: 直接调**(其他 service 需要时):

```python
from .long_task_service import get_long_task_service

class SomeService(ServiceBase):
    def do_something_with_context(self, thread_id: str):
        long_task = get_long_task_service()
        history = long_task.get_recent_messages(thread_id).data["messages"]
        # ... 用 history 做决策
```

**方式 B: 注入到 system prompt**(主路径,server.py chat_completions 已示范):

```python
skill_hints = long_task.match_skills(query).data["skills"]
if skill_hints:
    hint_text = "\n".join(f"- {s['title']}: {s['description']}" for s in skill_hints)
    messages.insert(0, {"role": "system",
                       "content": f"你可能用得上这些 Skill:\n{hint_text}\n用时调 long_task.load_skill(skill_id)"})
```

### 4.2 91 OpenAPI schemas 兼容

**完全不动**。`thread_id` 是新增可选字段,`extra="ignore"` 模式下旧请求带多余字段也不会 422。

**自动**:
- `req_models.py` 走 `_ModelBase` 的 `extra="ignore"`,旧客户端发新字段也不报错
- OpenAPI schema 自动重新生成(`scripts/pack_zip.py` 内已有 91 schema 验证)

### 4.3 跟现有存储的边界

- **不混表**: 现有 `request_logs` 存"每次请求",`thread_messages` 存"线程对话"。前者按时间,后者按 thread_id。
- **共用一个 SQLite 文件**: 减少运维复杂度,WAL 模式已就绪
- **共用 PRAGMA**: WAL + busy_timeout=5000,新表自动继承

---

## 5. 为什么不直接 clone Hermes 的 Skill 系统

| 维度 | Hermes Skill (Markdown) | MoA SKILL (JSON) | 不 clone 的原因 |
|---|---|---|---|
| 格式 | Markdown 自然语言 | JSON 结构化 | MoA 已有 Pydantic capability registry,JSON 跟 schema 同构,机器可读更好 |
| 触发匹配 | 关键词 + 向量(未来) | 关键词 + tag + 版本 | 1) 显式 trigger_patterns 数组比 LLM 自由发挥可控; 2) 强制 version 字段,防止 Skill 静默更新 |
| 审核 | 4 级 trust(builtin/official/trusted/community) | approved_by + approved_at 强制字段 | Hermes 装 community skill 直接可用,我们强制人工签字 |
| 自动生成 | Agent 跑完复杂任务自动写 | **永远人工写** | 1) 防止一次失误固化成长期默认; 2) MoA 没有"任务跑通 = 答案正确"的概念,强模型输出仍可能错; 3) 强制走 PR review 流程 |
| 容量 | SKILL.md 15KB 上限 | SKILL.json 5KB + references/ 不限 | JSON 主索引小,详细内容 references/ 按需 |
| 跨框架 | agentskills.io 标准 | 私有 + 引用 agentskills.io 概念 | 1) 兼容更好(主 JSON + 参考 Markdown); 2) 不绑死单一标准 |
| 持久化 | `~/.hermes/skills/` 用户级 | 仓库 `skills/` git 跟踪 | MoA 走企业级部署,Skill 必须跟代码同版本,不能用本地散落 |
| 线程安全 | 单进程 | 多 worker 并发(SQLite WAL) | Hermes 单 agent 进程,我们 4 worker 起步 |
| 加载方式 | fuzzy patch 修改 | 整个文件版本切换 | 1) JSON 整文件换比 Markdown patch 更安全; 2) 改坏了 git revert 就行,不用 fuzzy match 救火 |
| 错误恢复 | 安全扫描失败 → 回滚原版 | 安全扫描失败 → 不入库 | 我们没有"草稿版",失败就是失败,重审 |

**核心结论**:
- **学习的是思想**(Skill = 过程资产、复用优于重写、按需加载)
- **不学的是形式**(Markdown + fuzzy patch + 自动生成 + 4 级 trust)
- **我们更工程化**: JSON 强 schema + 强制审核 + git 跟踪 + 整文件版本化

---

## 6. 5 步实施路线

| 步骤 | 内容 | commit | 验收 |
|---|---|---|---|
| **Step 1** | `storage.py` 加 3 张新表 | `feat(long): add thread_sessions/messages/skills tables` | `python -c "from storage import get_storage; get_storage().conn().execute('SELECT 1 FROM thread_sessions')"` 不报错 |
| **Step 2** | `services/long_task_service.py` 完整实现 + 注册 | `feat(services): add LongTaskService (6 methods)` | 6 个 ServiceMethod 都能调,unit test 全过 |
| **Step 3** | `ChatCompletionRequest` 加 `thread_id` + server.py 端点头尾 13 行 | `feat(chat): thread_id opt-in for short-term memory` | 带 thread_id 的 chat 请求,SQLite 出现对应 messages;不带 thread_id 老请求 0 变化 |
| **Step 4** | 新增 4 个管理端点(`/v1/threads/*` + `/v1/skills/*`) | `feat(api): add /v1/threads + /v1/skills endpoints` | OpenAPI schema 增加到 95(91 + 4),e2e 测试通过 |
| **Step 5** | `skills/` 目录 + 3 个示例 Skill + `scripts/sync_skills.py` | `feat(skills): seed skills/ + sync script` | `python scripts/sync_skills.py` 把 3 个 SKILL.json 写入 thread_skills 表; `match_skills("部署状态")` 返回 1-3 个命中 |

**总工作量**: 5 个 commit,大约 600-800 行新代码(其中 200 行是 long_task_service,300 行是端点+req_models,200 行是示例 Skill + sync 脚本)。

**性能影响**:
- `chat_completions` 带 thread_id: +1 次 SQLite read (历史) + 1 次 write (回写)= ~2ms
- 不带 thread_id: 0 改动,0 影响
- `match_skills`: 简单关键词匹配,~1ms
- `load_skill`: 文件读 + safety scan,~5-20ms

**实测目标**:
- 长任务 chat RPS ≥ 400(单 worker,带 thread_id)
- 普通 chat RPS 保持 1477(不带 thread_id,0 影响)

---

## 7. 测试方案

### 7.1 Unit test (`tests/test_long_task_service.py`)

- [ ] create_thread → get_recent_messages 拿到刚追加的
- [ ] append_message 50 条 → 拿到 limit=20 时只回最近 20
- [ ] match_skills("部署状态") → 命中 deploy-check
- [ ] register_skill 后,match_skills 立即能命中
- [ ] load_skill 读不到的文件 → 报错而非崩
- [ ] 过期 thread 自动清理(改 expires_at 为过去)

### 7.2 集成 e2e (`perf/integration_long_task.py` 新)

- [ ] 场景 1: 创建 thread,跑 3 轮 chat,验证 SQLite 写入了 6 条消息
- [ ] 场景 2: 第 4 轮 chat,带 thread_id,验证 system prompt 注入了前 3 轮
- [ ] 场景 3: 跑 5 轮后,删 thread,验证 ON DELETE CASCADE 清空 messages
- [ ] 场景 4: 注册 5 个 Skill,query 命中其中 1-2 个,验证 system prompt 注入 hint
- [ ] 场景 5: 100 并发长任务 chat,验证 RPS ≥ 400,p95 ≤ 50ms
- [ ] 场景 6: 不带 thread_id 的老请求,验证 0 行为变化(向后兼容)

### 7.3 跟现有 e2e 兼容

- [ ] `perf/integration_e2e.py` 现有 104 场景 0 改动,0 失败
- [ ] `perf/bench.py` 现有 1477 RPS 保持
- [ ] `perf/chaos.py` 现有 19 场景 0 失败

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| SQLite 单文件写并发瓶颈 | 长任务 RPS 限制 | 跟现有 `request_logs` 共用,实测过 500 RPS 单 worker;真不够再分文件 |
| 长 prompt 撑爆上下文 | 100+ 小时项目 | 跟 Hermes 一样,>50% 触发"压缩中间保留首尾",从 long_task_service 加 `compress_thread()` |
| Skill 库膨胀 | 几百个 Skill 关键词匹配变慢 | trigger_patterns 简单正则,O(n) 扫描足够;真爆量加索引 |
| 自动生成的 Skill 不可控 | 用户绕过审核 | 我们强制人工写 + 审核,根本不让自动生成 |
| thread_id 滥用(无限创建) | SQLite 膨胀 | 默认 7 天 TTL,后台 `cleanup_expired()` 每天跑;配额可配置 |

---

## 9. 总结

- **不破坏现有任何东西**: 14 行 server.py + 1 行 Pydantic 字段 = 现有路径 0 行为变化
- **5 步可落地**: 每步一个 commit,每步有验收
- **学习 Hermes 思想不学形式**: JSON + 强制审核 + git 跟踪 = 工程化版
- **不跟 MoA 高 RPS 优势冲突**: 不带 thread_id 老路径保持 1477 RPS;带 thread_id 也能 400+ RPS
- **Phase 2 路线**(本方案不包含):
  - 向量检索匹配 Skill(sentence-transformers + FAISS)
  - 自动 Skill 生成(基于 chat 成功模式,但强制人工 review 才入库)
  - 跨 worker 共享 thread state(Redis 替换 SQLite)

---

*方案生成: 2026-07-19 / MoA Gateway Pro v1.8.1 / 不依赖任何外部组件,纯 SQLite + Pydantic*
