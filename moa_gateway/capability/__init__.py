"""moa_gateway.capability — 从 10 个参考项目迁移的能力集

来源项目:
01 gateswarm-router (TypeScript/Python — 智能路由)
02 MoA-together-ai (Python — MoA 算法核心)
03 MoA-Engine (Markdown — 元 Prompt 协议)
04 moa-main-commercial (Rust — 多租户/审计)
05 moa-skill (Python — CLI 反群体思维)
06 moai-adk-multiagent (Go — 多智能体框架)
07 moat-ops-auditor (Python — 编码守门员)
08 moa-server (Python — OpenAI 兼容 MoA server)
09 opencode-moa (Markdown + 配置 — 迭代 + 验证)
10 Verdex (Tauri/React — 桌面 4 段裁决)

子模块:
- auth: 多协议鉴权 (OpenAI/Anthropic/MCP) — 来源 01/02/04/08
- secret_scan: 9 类硬编码密钥检测 + 3 层豁免 — 来源 05/07
- moaflow: MoA 算法增强 (3 反群体思维 / 谄媚 / 仲裁) — 来源 05/09
- context_clean: 7 阶段消息清洗 + Plan/Act 检测 — 来源 01
- consensus: 集成投票器 + 边界再训练 — 来源 01
- cost_estimator: dry-run 成本估算 + dry-run 报告 — 来源 05
- score_panel: 5 维评分 (TQ/CO/AP/SE/IN) + multi-eval — 来源 09
- verdict_ui: 4 段裁决输出 (共识/碰撞/盲点/最终) — 来源 10
- model_context_db: 40+ 模型上下文窗口数据库 — 来源 10
- provider_rebalancer: 自愈 tier 重新平衡 — 来源 01
- gate_l0: L0 闸门 (不启 MoA 的机械验证任务) — 来源 05
- extract_anthropic: Anthropic system 提取 — 来源 10
- adaptive_quant: TurboQuant 压缩 v3.6 — 来源 01
- feedback_loop: feedback.json 反馈循环 + 边界再训练 — 来源 01
- dryrun_report: dry-run 报告 (cost/multiplier/confidence) — 来源 05
- prompt_registry: 16 内置提示词模板 (5 角色 × 2 语言 + 3 judge × 2 语言) — 来源 10
- i18n: 12 namespace i18n (en/zh) — 来源 10
"""
__version__ = "1.5.0"
