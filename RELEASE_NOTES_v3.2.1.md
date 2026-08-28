# MoA Gateway Pro v3.2.1 — 独立加固审计版（Production Hardening）

**Date:** 2026-08-29
**Base:** v3.2.0（orchestrator 增量）⊕ GitHub v3.1.1（完整基础设施）合并树
**Method:** 三路独立深审（安全+诚实性 / 架构 / 部署工程）→ 双向钢人论证 → 用户决策边界 → 分步加固 → 真实测试闭环 → 红蓝对抗复审
**验证口径:** 每项修复配真实回归测试；无法活体验证的项显式标注（零虚假政策适用于本文件自身）。

## 实测结果（Windows, Python 3.11, 本机验证）

| 项目 | 结果 |
|------|------|
| **全量 pytest** | **1170 passed, 0 failed**（合并基线 1089 + 新增 81 项加固回归，Windows/Py3.11 实测） |
| **Go 代理** | `go build` 0 错误 · `go vet` 干净 · **16 个测试函数（含 8 个 JWT 子用例）全绿**（修复前: 无法编译、零测试） |
| **活体 smoke** | 真实 uvicorn 后端 + 真实 Go 代理: Bearer mgw- key 全链路 → `X-Moa-Mock: true` 诚实标注响应 |
| **Docker/HA/Helm** | 静态修复+交叉审查；本环境无 Docker/K8s，**未活体验证** |

## 安全修复（真实漏洞，非合规润色）

- **SSRF 编码 IP 平台无关修复（P1）** — v3.1.1 依赖 glibc getaddrinfo 归一化编码 IP 字面量，Windows 解析器不归一化，且在 DNS 污染网络下 `http://2130706433/`、`http://0x7f000001/` 被当普通域名"解析成功"放行（项目自带测试在本机实测失败证实）。新增 inet_aton 语义归一化（十进制/十六进制/八进制/1-4 段/混合），DNS 之前判定，畸形数值主机 fail-closed；红队复审后追加 6to4/Teredo 过渡段与 trailing-dot FQDN 封堵。47 项回归测试全确定性（stub 解析器）。
- **orchestrator 授权一致性（P1）** — `/v1/orchestrator/run` 此前允许 readonly key 经"技能名提及"自动纳排路径触达 `code_execute`/`file_write` 等危险技能，违背项目自身文档化的"非 admin 不可达"信任模型。修复: DANGEROUS_TOOLS 单源化（冻结集，防热部署污染自撞）；红队复审进一步收紧: 非特权调用者不可执行*任何*沙箱技能（自定义 skill 与 code_execute 同沙箱，不能成为唯一边界），计划内诚实披露 `filtered_privileged_skills`；MCP 按真实角色判定（user 不再被折叠为 readonly）。18 项新回归。
- **skill 热部署重校验（P2）** — `load_persisted` 此前盲信持久化文件注册代码。现重放语法 + sanitize_code 静态校验（功能校验由每次调用的运行时沙箱承担，不在事件循环跑子进程），拒绝 builtin 名冲突与非法参数名，重名去重；测试不再污染生产 data/ 目录。
- **Go 代理无法编译（实锤缺陷）** — `extractClientIP` 被两处引用但从未定义。已实现（边缘语义: 直连对端为准）。
- **Go 代理误拒网关 API key（活体 smoke 发现）** — OpenAI 客户端按规范发送 `Authorization: Bearer mgw-...`，代理把一切 Bearer 值当 JWT 校验 → 整条数据面 401。现 mgw- 前缀转发后端鉴权。
- **Go 代理 XFF 欺骗防护** — 边缘丢弃客户端伪造的 X-Forwarded-For，重写为真实对端；RateLimiter 保留配置驱动可信代理判定。
- **登录限流 PG 兼容（P2）** — `login_attempts` upsert 走双后端但用 SQLite 专属 `INSERT OR REPLACE`，PostgreSQL 首次登录即语法错误。现方言感知（勘误: storage.py 主路径本有 is_sqlite 守卫，前审计过严——如实记录）。

## 诚实性修复

- **channels 三通道 mock 标注** — Subagent/CLI/API 通道的合成输出此前仅靠文本前缀区分。现 `ChannelResult.mock` 字段全链路标注（结果级 → 链级聚合 → orchestrator 透传）。
- **chat 失败指标记账** — `record_chat` 此前仅成功路径调用，5xx 错误率告警永不触发。现失败路径如实记账（内部指标，API 行为不变）。真实测试: REGISTRY 断言 5xx 计数递增。
- **web_search 谎言 docstring** — 声称 "Tavily → DuckDuckGo → Mock" 降级链，实际实现是诚实失败不伪造。文档改为与实现一致。
- **依赖/CI 真实化** — 移除零导入的 aiohttp 声明；pyproject 补齐 5 个实际导入却未声明的运行时依赖；CI 改用 requirements.txt（与镜像一致的真实发布面）；pip-audit 从 no-op 警告改为真拦截；sqlalchemy ≥2.0.36（SQL 注入公告修复版）。

## 部署产物可部署化

- **deploy/ha/Dockerfile.backend** — 移除 `COPY data/`（把 Fernet 密钥/SQLite/管理员密码烘焙进镜像，且新克隆构建必失败）；workers 4→1（进程全局态，多 worker 静默分片状态）。
- **HA compose** — 删除伪 postgres-replica（未配置复制、代码不读）与 Swarm-only `deploy.replicas`；Redis URL 从 sentinel 端口改为直连 master（应用无 Sentinel 客户端）；修复 Prometheus 挂载（此前引用不存在的 monitoring/prometheus.yml，且 rule_files 的 alerts.yml 未挂载会导致 Prometheus 启动中止）；Grafana 真实 provisioning（补 dashboard provider + datasource）；删除重复的 alert_rules.yaml（deploy/alerts.yml 为唯一真源）。
- **Helm chart** — 补缺失的 Secret 模板（此前必然部署失败）；readOnlyRootFilesystem 加 emptyDir /app/data（此前必然 crash-loop）；MOA_ADMIN_PASSWORD 注入；版本 1.8.1 → 3.2.1。
- **License 统一** — pyproject(Apache-2.0) vs LICENSE(MIT) 矛盾 → 统一 MIT。

## 已知现状警示（按"仅文档警示"决策保留，见 docs/SECURITY-HARDENING-GUIDE.md）

W1 /metrics/docs 未鉴权 + 0.0.0.0 默认绑定；W2 配额仅覆盖 chat/moa 路由；W3 RBAC 矩阵执行面惰性（fail-closed）；W4 admin JWT 兼作数据面 key；W5 SSRF resolve-then-connect TOCTOU 窗口；W6 三种错误信封并存。

## 明确未做（范围边界，用户决策）

DB 层异步化重写、routes/capability.py（3669 行）拆分、3 套 MoA 实现整合、services 层复活/删除、错误信封统一改造、metrics 默认鉴权。
