# moa_gateway_pro v4.1.0 集成开发计划表

**日期:** 2026-08-26 · **基线:** v4.0.0（262 端点 / 1435 测试全绿）
**目标:** 吸收三个开源项目的能力/资源/技术/经验/方案/方法，交付完整项目 + APK + Windows 程序。

## G0 决策记录（钢人论证摘要）

- **问题重述**：把 OmniRoute（路由/配额/压缩/免费目录）、OpenClacky（token 效率/skill 生态/子代理/IM）、MemoraX Code（跨会话记忆）三个 MIT 项目的可移植精华集成进本网关，并真实产出此前缺失的二进制交付物。
- **正向钢人**：三项目许可均为 MIT（可合法集成+署名）；其强项恰好对应本网关实测空白（跨会话记忆=空白点、路由策略仅智能路由、无响应头配额遥测、无堆叠压缩、无 IM 通道、无 skill 生态）；项目已有完备扩展点（router 注册、capability toggles、策略 registry、agent harness），集成成本可控。
- **反向钢人**：全量移植不现实（数万行、回归风险）；跨语言移植是"移植设计"不是"移植代码"，易做浅；APK/Electron 构建工具链本机缺失，需现场安装。
- **收敛决策**：① 只移植填补空白且有真实价值的能力，每项真实实现+测试+开关；② 旧基线 1435 测试零回退；③ 二进制构建为必交项，工具链先行安装；④ 版本号 v4.1.0；APK 无 keystore 走 build.gradle 已设计的 debug 签名回退并明示；Windows 出 NSIS+portable 双产物；MIT 署名写入 THIRD_PARTY_NOTICES.md。

## 集成矩阵（来源 → 模块 → 挂载点）

| # | 模块 | 来源 | 挂载点 | 验收 |
|---|------|------|--------|------|
| M1 | routing_strategies/ 路由策略引擎（19+1 策略 registry，纯函数排序器，接入 IntelligentRouter） | OmniRoute | router.py / config.routing | ≥40 测试 + /v1/routing/* 冒烟 |
| M2 | quota_scheduler/ 配额遥测+调度（QuotaValue、响应头解析、snapshots 表、自适应监控、can_afford 门、DRR+P2C quota-share） | OmniRoute | provider 选择前置 | ≥30 测试 + /v1/quota/* 冒烟 |
| M3 | compression/ 堆叠压缩（off/lite/standard/aggressive/ultra/rtk/stacked；RTK 工具输出压缩 + Caveman 规则瘦身 + 保真门 + 统计） | OmniRoute | chat 前置（toggle） | ≥35 测试 + /v1/compression/* 冒烟 |
| M4 | free_tiers/ 免费额度目录（OmniRoute 456 条真实 catalog 数据移植为 JSON 资源 + poolKey 去重聚合 + regime 分层） | OmniRoute（资源） | 独立 capability | ≥15 测试 + /v1/free-tiers/* 冒烟 |
| M5 | A2A 服务器（agent.json + JSON-RPC 2.0 + 5 skills 调真实内部） | OmniRoute | 独立路由 | ≥15 测试 |
| M6 | efficiency/ token 效率 harness（不可变 system prompt + 注入旁路、双缓存标记、Insert-then-Compress、空闲压缩调度器、命中率指标） | OpenClacky | assistant 会话 | ≥30 测试 |
| M7 | skillhub/ skill 生态（SKILL.md frontmatter、多源加载、模糊搜索、invoke_skill 元工具、自然语言创建、自进化钩子、内置技能包） | OpenClacky | ToolHub + 独立路由 | ≥35 测试 + CRUD 冒烟 |
| M8 | channels/ IM 通道层（适配器抽象 + 标准事件 + 会话路由；Telegram/Feishu/DingTalk/WeCom/Discord 真实协议实现，配凭据即生效） | OpenClacky | 独立路由 | ≥30 测试 + CRUD 冒烟 |
| M9 | 子代理路由（fork prefix + lite_models 映射 + forbidden_tools + 摘要折叠 + 成本合并） | OpenClacky | agent harness | ≥15 测试 |
| M10 | memory/ 跨会话记忆层（五型记忆 + 作用域 + 混合召回配方 + 脱敏/缓冲/分块回写管线 + hook 协议 + assistant 集成） | MemoraX | assistant + 独立路由 | ≥40 测试 + 冒烟 |
| M11 | workspace memory（.moa_memory 工作区知识层 + facets 收集脚本 + 更新策略 + supervisor 锁） | MemoraX | memory 子模块 | ≥15 测试 |
| M12 | admin-ui 新页面（路由/配额/压缩仪表盘、免费目录、记忆浏览、skill 管理、通道管理，全 CRUD+配置） | 三线 | admin-ui/ | next build 通过 |
| M13 | mobile 1.1.0（Insights 页接新端点）+ APK 构建 | — | mobile/ | APK 产物 + aapt2 验证 |
| M14 | desktop 4.1.0（内嵌新控制台 + 版本） | — | desktop/ | electron-builder 产物 |
| M15 | 版本/文档（版本单源→4.1.0、CHANGELOG、RELEASE_NOTES、THIRD_PARTY_NOTICES、DELIVERY_REPORT） | — | 仓库根 | 一致性核查 |
| M16 | 构建与交付（全量回归→wheel/sdist→APK→NSIS/portable→release 目录→最终 review） | — | release/ | 证据链完整 |

## 执行编排

1. **共享契约先行（主线）**：capability_toggles 新增 9 开关；config.py/config.yaml 新增 9 个配置节。此后并行 Agent 只新建自己的模块文件与测试文件，不触碰共享文件，杜绝冲突。
2. **并行实现（6 Agent 集群）**：A=M1+M2 · B=M3+M4 · C=M6+M9 · D=M7+M8 · E=M10+M11 · F=M5。
3. **接线（主线）**：server.py include_router + routes/__init__ 导出 + assistant/chat 集成点 → 全量回归 → 冲突修复。
4. **双 AI 互审**：每模块交付后独立 Reviewer 对抗复审（grep 零虚假扫描 + 测试真实性核查），问题闭环。
5. **前端（M12–M14）**→**构建（M16）**→**G5 审计与最终 review**。

## 硬边界

- 零 mock/桩/占位进交付物；测试目录内受控 mock 须有边界说明。
- 新模块全部 feature toggle，默认安全（压缩/记忆注入等数据变更类默认 opt-in，对齐 OmniRoute PII opt-in 纪律）。
- MIT 署名：THIRD_PARTY_NOTICES.md 列三项目版权与许可文本摘要。
- 回归基线只增不减。
