# MoA Gateway Pro — 自定义 Prompt 模板

目录里的 .md 文件会被 MoA Orchestrator 自动加载,优先级高于内置默认。

## 文件名(按 strategy)

- `aggregator.md` — 聚合器聚合多模型答案时用的 system prompt
- `critic.md` — Critic 互审员用的 prompt
- `compose_{role}.md` — Compose 模式下每个 role 的 prompt
  - `compose_feasibility.md` — 可行性视角
  - `compose_performance.md` — 性能视角
  - `compose_security.md` — 安全视角
  - `compose_ux.md` — UX 视角
  - `compose_architecture.md` — 架构视角
  - `compose_business.md` — 业务视角
- `judge_reflection.md` — Judge 模式反思 prompt
- `chain_{step}.md` — Chain 模式每步的 prompt
  - `chain_research.md`
  - `chain_analyze.md`
  - `chain_summarize.md`

## 模板语法

支持 `{placeholder}`:
- `{user_query}` — 用户原始问题
- `{reference_responses}` — 所有参考模型的答案(自动拼接)
- `{original_messages}` — 原始对话历史(JSON)
- `{aspects}` — Compose 模式下其他视角的概要
- `{current_draft}` — Critic 修订时用,当前草稿

## 如何添加自定义模板

```bash
# 方式 1:在配置目录创建文件
mkdir -p ~/.moa-gateway/prompts
cp my-aggregator.md ~/.moa-gateway/prompts/aggregator.md
# 重启网关生效,或通过 WebUI 热更

# 方式 2:在 moa_gateway/prompts/ 创建自定义文件覆盖默认
```

## 示例:自定义 aggregator

```markdown
你是工业级多模型答案聚合器。

要求:
1. 必须给出 3 个方案,标注各自的优劣
2. 每个方案必须可执行(给到具体步骤)
3. 风险标注:必须列出潜在风险与缓解
4. 简洁:总字数不超过 1000 字
```

## 重启 vs 热更

重启:文件保存后重启网关立即生效。
热更:通过 WebUI 提示词管理面板(即将推出)实时生效。
