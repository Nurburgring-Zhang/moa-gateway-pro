# MoA Gateway Pro v3.2.1 生产加固规划（Production Hardening Plan）

> 制定日期: 2026-08-28
> 基线: 合并树 = GitHub v3.1.1 全部基础设施 + v3.2.0 orchestrator 增量（v3.2.0 经比对为 v3.1.1 严格超集，已合并）
> 实测基线: pytest 1089 收集（预期 1087 pass + 2 SSRF Windows 失败，验证中）
> 用户决策边界: ①生产加固线（不做大重构）②不改默认行为（安全收紧只做文档警示）③装 portable Go、不装 Docker

## 0. 方案对比记录（多方案并行评估结论）

| 方案 | 内容 | 结论 |
|---|---|---|
| A. 仅修 P0/P1 止血 | 修 test_report 缺失 + SSRF + 依赖 CVE | 否：部署产物仍不可用，不满足"完全强化" |
| B. 全量理想化 | + async DB 重写/拆巨石文件/合并3套MoA/RBAC重设计 | 否：98K LOC 下不可真实验证，违反零虚假 |
| **C. 生产加固线（选定）** | 真实漏洞修复 + 部署产物可部署化 + Go 真编译 + 文档真实化；行为变更仅文档警示 | 可行性最强：每步可真实测试回归 |

## 1. 阶段与步骤（每步 15~30 分钟，完成即压缩总结进 HARDENING_STATE.md）

### Phase 1 — 真实漏洞修复（Python 侧）
- **S1 F1 SSRF 编码 IP 平台无关修复**（P1）
  - 内容: `moa_gateway/utils/url_validator.py` 在 getaddrinfo 之外增加平台无关的 IP 字面量归一化（十进制整数 2130706433、十六进制 0x7f000001、八进制 0177.0.0.1、混合点分、IPv4-mapped IPv6），任何等价内网/回环地址一律拒绝。
  - 验收: `pytest tests/test_v311_fixes.py::TestSSRFValidator` 全绿（Windows 实机）；新增 test_ssrf_hardening.py 覆盖 ≥12 个绕过向量；全量回归不引入新失败。
- **S2 orchestrator P1 授权一致性**（P1）
  - 内容: `/v1/orchestrator/run` 对非 admin/operator 调用者过滤 DANGEROUS_TOOLS 类 skill（与 routes/agent.py 信任模型一致）。注意：这是修复"实现与自身文档承诺不一致"，属对齐既有声明，不是新增收紧。
  - 验收: 新增测试：readonly key 经 name-mention 路径请求 code_execute → 403/被拒；README 声明与行为一致。
- **S3 orchestrator P2 加固**: load_persisted 重放完整校验管线（AST+sanitize+功能试跑）；skill_factory 禁止覆盖 builtin 名；测试隔离（tmp_path + 不污染 data/）。
- **S4 诚实性补全**: capability/channels.py 的 `[subagent]`/CLI 通道结果补 `mock:true` 字段 + 响应头；web_search.py 谎言 docstring 改为与实现一致。
- **S5 指标真实性**: routes/chat.py record_chat 失败路径也如实记账（5xx 状态码计数，仅内部指标，不改 API 响应）；deploy/alerts.yml chat 错误率告警随之可真实触发。

### Phase 2 — 部署产物可部署化（静态修复 + 交叉验证）
- **S6 deploy/ha/Dockerfile.backend**: 移除 `COPY data/`（构建必失败项）；workers 4→1 并注释单进程全局态约束。
- **S7 HA compose 修正**: prometheus.yml 挂载路径与 rule_files/alerts.yml 一致化（统一 deploy/alerts.yml 为唯一真源，删除 monitoring/alert_rules.yaml 重复）；redis sentinel URL 修正（代码无 Sentinel 支持 → 改普通 redis + 文档说明）；删除伪 replica / DATABASE_REPLICA_URL 或标注未实现。
- **S8 Helm chart 可部署化**: 补 Secret 模板；readOnlyRootFilesystem 加 emptyDir 挂载 /app/data；版本 1.8.1→3.2.1。（无 Docker/K8s 环境 → 修复逻辑 + kubeconform 级静态自检，文档标注"未活体验证"）
- **S9 依赖治理**: requirements.txt sqlalchemy>=2.0.36（CVE-2024 系列修复版）；pyproject/requirements 漂移对齐（sqlalchemy 下限一致、补 aiofiles、aiohttp 处理：代码未导入 → 从 requirements 移除并在文档说明）；CI pip-audit 失败拦截（`|| echo` → 真失败）。
- **S10 PG 方言兼容**: storage.py:721 / rag_search.py:276 / routes/auth.py:58 的 INSERT OR REPLACE → 方言感知 upsert（SQLite 保持原样，PG 用 ON CONFLICT；database.py 现有 _convert_placeholders 链路内实现）。验收: 转换函数单测 + SQLite 全量回归无新失败（无 PG 实例，PG 路径以单元级真实验证转换输出 SQL 正确性，文档标注未连真库）。

### Phase 3 — Go 代理真实化
- **S11 编译与单测**: portable go1.27 编译 proxy/（零告警）；新增 auth_test.go（JWT 合法/过期/篡改/alg=none）、ratelimit_test.go、forward_test.go 真单测；`go vet` 通过。
- **S12 BACKEND_URLS 多后端**: 实现 env 驱动多后端轮询（未设 env 时行为与现状完全一致=单 backend，不破坏默认）；活体 smoke：真实启动 uvicorn 后端 + go 代理转发请求成功。
- **S13 活体验证**: python 后端 127.0.0.1:8088 + go proxy :8080，真实打 /health /v1/models /docs 鉴权矩阵请求。

### Phase 4 — 文档真实化与版本
- **S14 README/CHANGELOG/RELEASE_NOTES v3.2.1**: 测试真实数字（Windows+Linux 差异说明）、SSRF 修复记录、orchestrator 授权模型澄清、警示文档（配额覆盖/metrics 暴露/RBAC 现状/DNS rebinding/错误信封）；license 统一 MIT； Helm/compose 版本号同步。
- **S15 SECURITY-HARDENING-GUIDE.md**: 生产部署必改项清单（绑定、CORS、metrics 鉴权建议、配额策略、API key 管理）——承载用户决策②的全部警示内容。

### Phase 5 — 对抗审核与回归
- **S16 双AI对抗审核**: 红队 subagent（只审 diff，找绕过/虚假）+ 蓝队 subagent（验每条修复声明 vs 代码），发现项回修。
- **S17 全量回归 + 活体探针**: pytest 全量（目标: 0 fail）；真实启动服务跑 SSRF 探针/编排探针/mock 标注探针；ruff/mypy 过 CI 门。
- **S18 最终复盘**: 本文件更新 + 交付报告（含"未验证项诚实清单"）。

## 2. 每步通用标准
- 每步完成 = 实现 + 真实测试证据 + HARDENING_STATE.md 压缩总结更新。
- 任何无法在本环境真实验证的项 → 显式标注"未活体验证"，禁止冒充已验证。
- 不改变默认行为（S2 例外：对齐项目自身已文档化的信任模型）。
- 全量 pytest 是每步回归底线：不允许新增失败。

## 3. 边界（明确不做）
async DB 层重写；拆 routes/capability.py；合并 3 套 MoA 实现；services 层复活/删除；错误信封统一；/metrics 默认鉴权；全路由配额强制 —— 全部仅文档警示（W1-W6）。
