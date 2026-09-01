# MoA Gateway Pro v4.2+ 增强计划表（对标三仓库 + GitHub 主线）

**日期:** 2026-08-30 · **依据:** GitHub v3.2.1 全量文件 diff、OmniRoute 架构深档
（agents.md：三层韧性/19 策略/15 因子评分/MCP 110 工具）、v4.1 双盲审核报告、
DELIVERY_REPORT_v4.1 后续建议
**方法:** 每项 15–30 分钟小步落地，先探针/依赖核查再动手，完成即测，双 AI 互审

## 已完成（v4.2.0，随本版本交付）

| # | 项 | 来源 | 状态 |
|---|---|---|---|
| 1 | **M13 自主编排引擎**（O1-O6：注册表/分析/规划/执行/强化/技能工厂，1898 行 + 42 测试） | GitHub v3.2.1 | ✅ 已合并接线，42/42 绿 |
| 2 | **F-1 subagent runner**（真实 ModelPool 执行） | 盲审甲 | ✅ 已修复 |
| 3 | **M-2 webhook fail-closed**（Telegram/飞书/Discord） | 盲审乙 | ✅ 已修复 + 3 守卫 |
| 4 | admin-ui orchestration 页面 | GitHub v3.2.1 | ✅ page.tsx 已复制（待 next build 验证） |

## 计划项（按 价值/工作量 排序）

### P1 免费层总量看板（S，半天内）
- `GET /v1/free-tiers/totals`：暴露 catalog `compute_totals`/`pool_representatives`
  （catalog.py 已实现无 HTTP 面）；admin-ui free-tiers 页加汇总卡
- **验收:** 真实 HTTP 测试 + admin-ui build 绿；**来源:** DELIVERY_REPORT 建议 3

### P2 OmniRoute 三层韧性机制（L，2-3 天）
- provider 熔断器（CLOSED/OPEN/HALF_OPEN，408/5xx 触发、401/403/429 不触发）、
  连接冷却（base×2^n 指数退避 + Retry-After 优先）、模型级锁定（同连接其他模型继续服务）
- 落点：`moa_gateway/routing_strategies/resilience.py` 新包 + TelemetryStore 接线 +
  config.yaml `resilience:` 节（全默认关）
- **验收:** 每机制 ≥15 例确定性单测 + /v1/routing/resilience 状态端点；**来源:** OmniRoute
  `src/shared/utils/circuitBreaker.ts` + `open-sse/services/accountFallback.ts` 语义移植

### P3 MemoraX 记忆治理增强（M，1 天）
- 记忆衰减（access_count/last_access 指数半衰期权重）、冲突消解（同 scope 同 key
  新旧合并策略）、检索重排（recency×relevance 混合）
- 落点：`moa_gateway/memory/` 扩展；**验收:** 单测 ≥20 例；**来源:** MemoraX 治理层

### P4 压缩中文规则包（M，1 天）
- `rules/zh/`：中文填充词/冗余敬语/重复句式五类规则（对齐 en 包结构），
  caveman 引擎按语种路由加载
- **验收:** 中文样本压缩单测 ≥20 例 + fidelity 闸门行为与英文一致；**来源:** OmniRoute
  Caveman 架构（en 包为参照，zh 为自研扩展——如实标注非上游移植）

### P5 发布工程（M，依赖用户提供材料）
- 正式 keystore（MOA_KEYSTORE_*）→ 可上架 release APK；Authenticode 证书
  （MOA_SIGN_PFX_*）→ 签名 exe；至少一个真实 provider key → 全链路真实调用验证
- **阻塞:** 需主人提供 keystore/证书/key；未提供前产物保持 debug/unsigned 如实标注

### P6 OmniRoute 深档候选池（L，另行评审后立项）
- Auto-Combo 15 因子评分精细化（当前 auto 策略 12 因子）、组合熔断面板、
  free-model radar 覆盖层、MCP 工具池 gamification（低优先）
- **准入:** P2 落地且回归绿后评审

## 边界与纪律
- 一切新能力走 capability 开关、默认关、关闭即 503；零 stub/mock/placeholder
  （显式 X-MOA-Mock 标注体系除外）；每项完成即 CHANGELOG + 测试 + 双审
- 参考仓库只读；移植注明来源与降维点；自研注明自研
