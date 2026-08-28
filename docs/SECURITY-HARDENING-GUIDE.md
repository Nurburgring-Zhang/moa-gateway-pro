# 生产安全加固指南（SECURITY HARDENING GUIDE）

> 适用版本: v3.2.1 ｜ 2026-08-29 ｜ 本指南承载 v3.2.1 审计确认、但按"不改变默认行为"决策**保留现状**的风险项（W1-W6）。
> 每一项都给出了具体缓解配置。生产部署前必须逐项过一遍。

## W1 — /metrics、/metrics/traces、/docs 未鉴权 + 默认绑定 0.0.0.0

**现状**: `/metrics`（Prometheus 指标：endpoint 健康度、成本计数、缓存统计）、`/metrics/traces`（近期 trace span）、`/docs`、`/redoc`、`/openapi.json` 无需鉴权即可访问；服务默认绑定 `0.0.0.0`（config.yaml `server.host`）。任何能连通的网内主机可枚举全部 243 个路由与内部拓扑。

**缓解（生产必做）**:
1. `config.yaml` → `server.host: 127.0.0.1`（配合反向代理对外）；容器部署通过端口映射边界控制。
2. 在边缘（Go 代理/Nginx）封禁 `metrics`/`docs` 路径的外网访问，Prometheus 抓取走内网。
3. 或在网关前放置仅内网可达的独立监听实例专门服务 /metrics。

## W2 — 配额/限流仅覆盖 chat 与 moa 路由

**现状**: per-key RPM 与日 token 配额仅在 `/v1/chat/completions` 与 `/v1/moa/*` 生效。`/v1/agent/*`、`/v1/capability/*`（78 个）、`/v1/benchmark/*/run`、`/v1/assistant/*` 等会消耗 provider token 的路由**不扣配额**。持任意 `mgw-` key 者可造成不受控的 LLM 开销。

**缓解（生产必做）**:
1. 只把 key 发给可信调用方；为每个 provider 设硬性消费上限（provider 侧 billing limit）。
2. 边缘层对 `/v1/` 全前缀加 IP 级限流（Go 代理令牌桶已覆盖，但按 IP 不按 key）。
3. 代码修复方向：将 `get_limiter().check_and_incr/ incr_tokens` 接入所有触达 `pool.call` 的路由（约 6 个路由模块）。

## W3 — RBAC 矩阵存在但执行面惰性

**现状**: 4 角色/15 权限矩阵已实现，但路由层只区分 admin 与非 admin（`require_admin`），`require_permission` 装饰器当前无调用点。operator/user/readonly 角色可登录 JWT，但除 `/v1/` 数据面外访问不到管理面——**fail-closed，安全方向正确**，但"企业级 RBAC"宣传与实际执行面不符（orchestrator 已在 v3.2.1 对齐该信任模型）。

**缓解**: 生产中只创建 admin 角色账号；不要宣传/依赖 operator/user 细粒度授权，直到 `require_permission` 真正接线。

## W4 — Admin JWT 兼作网关 API key（quota 999999）

**现状**: admin 登录签发的 JWT 同时通过 `/v1/*` 数据面鉴权，且 per-key RPM 配额按 999,999 处理（`auth.py`）。任何导致 admin JWT 泄漏的日志/代理/浏览器漏洞同时打开数据面。

**缓解**: admin JWT 仅用于管理台会话，不要把它配置到应用调用方；应用调用一律使用 `mgw-` key；避免在日志中打印 Authorization 头。

## W5 — SSRF 守卫存在 resolve-then-connect（DNS rebinding TOCTOU）窗口

**现状**: v3.2.1 已修复编码 IP 字面量在 Windows/污染 DNS 下的绕过（十进制/十六进制/八进制在 DNS 之前归一化判定）。但校验时的 DNS 解析与实际连接时的解析是两次独立查询——攻击者持有一个在两次查询间切换 A 记录的域名，理论上可穿透校验连到内网（缓解因素：所有出站调用 `follow_redirects` 均未开启）。适用于 MCP server URL 注册、多模态 URL 输入等一切外链入口。

**缓解**: 高安全环境将出站流量收敛到 egress 代理/防火墙白名单；代码修复方向是 resolve 后 pin IP 连接（需自定义 httpx transport）。

## W6 — 三种错误响应信封并存

**现状**: Starlette 默认 `{"detail": ...}`、OpenAI 风格 `{"error": {...}}`（超时中间件/SSE）、agent 调度器 `{"ok": ..., "error": ...}` 并存。客户端需按前缀区分。属兼容性负担而非漏洞。

**缓解**: 客户端集成按路径族区分处理；`/v1/*` 优先按 OpenAI 错误信封解析。

## 已在 v3.2.1 真实修复（无需再配置）

- **F1 SSRF 编码 IP 绕过**（平台无关归一化，含 Windows + 污染 DNS 场景；回归测试 `tests/test_ssrf_hardening.py`）
- **orchestrator 授权一致性**（非特权调用者经 name-mention 路径不可再触达 code_execute/file_*/api_verify；planner 过滤 + executor 纵深防御 + MCP 角色检查）
- **skill 热部署重校验**（load_persisted 重放语法/安全/沙箱功能三重校验；builtin 名保护）
- **Go 代理两处缺陷**（extractClientIP 未定义导致无法编译；OpenAI 风格 `Bearer mgw-` key 被误拒——活体 smoke 发现）
- **Go 代理 XFF 信任**（边缘丢弃客户端伪造的 X-Forwarded-For，重写为真实对端地址）
- **chat 失败指标记账**（5xx 现在真实计数，错误率告警可触发）
- **HA compose / Helm / Dockerfile.backend 可部署化**（详见 RELEASE_NOTES_v3.2.1.md）

## 验证状态诚实声明

- Python 侧（SSRF/授权/沙箱/诚实性/指标/PG 方言路由）：本机 Windows + Python 3.11 **真实回归验证**（pytest 全绿）。
- Go 代理：**真实编译 + go vet + 16 个测试函数（含 8 个 JWT 子用例）全绿 + 与真实 uvicorn 后端活体 smoke 全链路打通**。
- Docker/HA compose/Helm：**静态修复 + 交叉审查**，本环境无 Docker/K8s，**未活体验证**，部署前请先在预发环境验证。
