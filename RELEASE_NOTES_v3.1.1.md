# MoA Gateway Pro v3.1.1 — 十轮全量审计修复版（P0/P1 清零）

**Date:** 2026-08-16
**Status:** Production-hardened — 全量审计发现的全部 P0/P1 已修复并经活体复验

本版本对 v3.1.0 执行了**十轮全量审计**（243 端点扫描、6 路并行代码深审、本地真实
LLM 链路注入、浏览器 E2E、chaos 故障注入、对抗性复审），并以**零虚假容忍**标准修复了
全部确认缺陷，随后经对抗性复审二轮再挖再修。

## 验证结果
- **单元测试:** 1071 passed, 0 failed（v3.1.0 为 593；新增 478 例审计回归）
- **活体探针:** 一轮 41/41 + 二轮 4/4 全过（沙箱/SSRF/越权/mock 标注/配额/GDPR/真实链路）
- **前端:** `next build` 通过，14 页静态生成，tsc 0 错；E2E 硬刷新会话保持 6/6
- **对抗性复审:** 一轮 19 项修复 13 证实/6 部分证实/0 证伪；二轮新发现 2 P1 + 4 P2 已全部修复

## P0 修复（一票否决项）
- **Agent 沙箱逃逸 RCE** — v3.1.0 的 AST 黑名单可被 `operator.attrgetter('__builtins__')`
  一击穿透（实测达成子进程 RCE + 任意文件读）。v3.1.1 三层加固：
  1. AST 层封禁全 dunder 属性 / subscript dunder 键 / format 属性遍历 / **任意含 dunder 的字符串字面量**
  2. 导入白名单移除 `operator`、`string`（运行时属性遍历原语）
  3. 运行时模块代理 `_ModuleProxy` 封禁一切 dunder 动态访问（拦截 chr() 拼接攻击）
  4. 危险工具（code_execute/file_*/api_verify）仅 admin/operator，API-key 用户 403
  - 对抗复审全部逃逸 payload（attrgetter、format 拼接、chr 构造）实测均被拦截

## 安全 P1 修复
- **secret-scan** 提权 admin + commonpath 限定 + 源头脱敏（不再回显密钥原文）
- **in-flight** 忽略调用方 state_dir（封堵任意目录写原语）
- **health restore/purge** 提权 admin + EndpointUpsert 严格校验
- **moa prompts PUT/DELETE** 提权 admin（封堵跨租户提示词注入）
- **SSRF 统一强化** — DNS 全解析、编码 IP、IPv4-mapped IPv6、fail-closed；
  **显式拉黑 IANA 特殊段含 RFC 6598 CGNAT 100.64.0.0/10（阿里云元数据 100.100.100.200 所在段）**

## 诚实性 P1 修复（D6 显式 mock 政策闭环）
- MoA 全链路 provider 追踪，`/v1/moa/execute` 返回 `X-MOA-Mock` 头 + `mock` 字段
- MoA 渐进流式补 mock 头；channels / reference-router 显式 mock 标注
- MoA 参考模型全失败 → 显式 502 + 逐模型失败证据（不再静默降级）
- **缓存命中重放 mock 标注**（修复 mock 输出经缓存后丢标注）

## 功能 P1 修复
- **服务层死方法清零** — 60+ 处 ImportError/签名错配（含 self_heal promote/demote 错接线）改走真实实现
- **GDPR 被遗忘权真实生效** — 真删 admin_users、按 key_id 匿名化日志、加盐 HMAC 不可彩虹表还原、清理 user_id 残留
- **流式配额计费** — `stream=true` 计入每日 token 配额
- **MoA 高耗端点限流** — similarity/flask/benchmark/cost-pareto 补 RPM + token 计费
- **请求模型真实类型化** — 85 个模型 `extra=forbid`（未知字段 422）

## 打包与版本
- wheel 补数据文件：prompts/、workflows/builtin/、webui/、param_templates/、migrations
- 版本号四处统一 3.1.1（`__init__`/pyproject/openapi/health）
- 前端 admin-ui 会话修复：硬刷新/深链不再丢登录态

## 诚实性说明（零虚假政策，延续 v3.1.0）
- 多模态/搜索/重排等能力在无真实 provider key 时返回**显式标注**的合成结果
  （`X-MOA-Mock: true` / `mock:true` / `[Mock]` 前缀），配置真实 key 后优先真实 provider。
- v3.1.1 进一步保证：mock 输出经缓存重放仍带标注；MoA 全失败显式 502 不冒充成功。
- 外部 MCP stdio 传输在本部署形态诚实返回 unsupported。

## 升级须知
- 生产部署仍需通过环境变量提供 `MOA_ADMIN_PASSWORD`、`MOA_GATEWAY_KEY`、`MOA_JWT_SECRET`。
- 从 v3.1.0 升级：直接替换包即可；缓存中 v3.1.0 旧条目（无 mock 信封）按 legacy 处理，TTL 过期后自然替换。
