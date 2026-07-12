# 06 · MoA 编排深度解析

## 6.1 为什么需要 MoA?

单个大模型有三个**老毛病**(OpenSquilla v0.5.0 总结):

1. **一个人查资料,容易漏关键信息源** — 单一检索路径
2. **一个人算数,没人帮着对,容易算错** — 缺乏交叉验证
3. **一个人干活,顾得了这头顾不了那头** — 注意力分散

MoA 的核心思路:**用工程手段 — 多样性采样 + 共识聚合 — 把单模型短板垫平**。

```
单模型 = 一个人干活
        │
        ▼
        容易遗漏、容易算错、注意力分散

MoA = 4 个模型并行提案
        │
        ├─→ 模型 A(从语义角度)
        ├─→ 模型 B(从代码角度)
        ├─→ 模型 C(从架构角度)
        └─→ 模型 D(从用户场景角度)
                │
                ▼
            共识聚合
                │
                ▼
            互审多轮
                │
                ▼
            修订输出
```

## 6.2 借鉴来源

### 6.2.1 Hermes v0.18.0 MoA 模型委员会

Hermes 的 MoA 设计:
- **参考模型不带 tool schema**,只看 user/assistant 文本 — 防止工具调用过载
- **聚合器拿全部 context + tool schema** — 真正决定"行动"
- 每次主模型调用 = 一次完整 MoA 循环

**我们的改进**:
- 增加**互审员**(用另一个不同模型审查聚合输出)
- 共识度低时**自动追轮**(OpenSquilla 没做)
- 完整可观测的请求日志

### 6.2.2 OpenSquilla v0.5.0「4 国产 + 1 聚合」

OpenSquilla 的实测结论:
- 4 个国产便宜模型(DeepSeek v4 / GLM-5.2 / Kimi K2.7 / Qwen 3.7)并行
- 1 个聚合器(GLM-5.2)综合
- 在 DRACO 100 任务上**质量比 Opus 4.8 +8.59、成本 -40%**

**我们的实现**:
- 参考模型**跨 provider 优先**(避免同一 provider 全挂的风险)
- 聚合器可选 **flagship**(claude-opus / gpt-5.x / GLM-5.x)
- 成本可观测(每个调用都记)

### 6.2.3 互查互审(critic 模式)

我们额外加的:
- 共识分数 < 0.35 → 自动追加 critic 轮
- critic 用**不同于 aggregator** 的模型,避免利益冲突
- 输出 JSON 格式 `issues / suggestions / verdict`

## 6.3 四种 Preset 详解

### 6.3.1 `fast` — 省钱模式

```yaml
strategy: single
reference_count: 1
aggregator: ""
critic_rounds: 0
tier: lite
```

**适用**:问候、简单问答、翻译、改写
**成本**:1×
**延迟**:低
**质量**:单 lite 模型

### 6.3.2 `balanced` — 平衡模式(推荐默认)

```yaml
strategy: parallel
reference_count: 4
aggregator_tier: premium
critic_rounds: 1
```

**适用**:通用问题(代码、设计、文档、分析)
**成本**:~4× 1 个 premium 模型
**延迟**:中等
**质量**:≥ 旗舰单模型

### 6.3.3 `quality` — 高质量模式

```yaml
strategy: parallel
reference_count: 5
aggregator_tier: flagship
critic_rounds: 2
```

**适用**:复杂决策、深度分析、关键代码审查
**成本**:~5× flagship + 2 互审
**延迟**:高
**质量**:≥ 任何单模型

### 6.3.4 `pipeline` — 流水线模式

```
[planner:premium] → 生成任务规格 spec
        │
        ▼
[generator:standard] → 按 spec 产出
        │
        ▼
[evaluator:premium] → 审查/修订
        │
        ▼
PASS 或 修订版
```

**适用**:长链路、需要"先想清楚再做"的任务
**成本**:~3×(premium+standard+premium)
**延迟**:高
**质量**:可解释性强(planner 输出可审计)

## 6.4 共识分数学

我们用 **Jaccard 相似度**衡量多个参考模型的"意见一致性":

```python
def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b)

# 例:
# 模型 A 关键词 {distributed, consistency, partition, latency}
# 模型 B 关键词 {distributed, consensus, partition, fault-tolerance}
# 交集 {distributed, partition} = 2
# 并集 {distributed, consistency, partition, latency, consensus, fault-tolerance} = 6
# Jaccard = 2/6 = 0.33
```

**共识度** = 所有参考模型对之间 Jaccard 的平均。

**触发追轮**:
- 共识度 < 0.35 → 追加 critic 轮
- critic 提出的问题数 > 0 → 让 aggregator 修订
- 直到 critic 没新问题,或达到最大追轮次数

## 6.5 关键边界:Hermes 风格

| 角色 | 看什么 | 不看什么 | 目的 |
|---|---|---|---|
| 参考模型 | user/assistant 文本 | system prompt(可选)、tool schema、工具调用记录 | 减少 token、避免严格 provider 拒绝 |
| 聚合器 | 全部 context + tool schema | - | 真正决策 |
| 互审员 | 聚合后的输出 + 原始参考 | - | 找问题 |

**好处**:
- 参考模型调用便宜(没有 tool schema 的 token 成本)
- 决策权集中在聚合器,行为可预测
- 互审员独立于聚合器,避免利益冲突

## 6.6 实测建议

### 6.6.1 起步阶段

1. 接入 **2 个国产便宜模型**(DeepSeek v3 + Qwen Plus) + 1 个旗舰(Claude Sonnet)
2. 用 `balanced` preset
3. 跑 5-10 个真实问题,看效果
4. 调 tier、weight、preset

### 6.6.2 调优

- **质量不够**:换 `quality` preset,或加更多参考模型
- **成本太高**:换 `fast` preset,或减小 reference_count
- **延迟太高**:减小 critic_rounds,或加更小的 critic 模型
- **某 provider 经常挂**:进 WebUI 看健康检查,停用或加 fallback

### 6.6.3 监控

WebUI 「仪表盘」看:
- 总请求 / 总 token / 总成本
- 共识度分布(过低 = 参考模型质量参差)
- 互审触发率(高 = 经常需要修订,可能 preset 选错了)
- 各模型调用次数(单一模型占比过高 = 没真正分散)

## 6.7 与单模型对比(经验值)

> 仅供参考,实际效果取决于任务类型和具体模型。

| 任务类型 | 单旗舰模型 | MoA balanced(4+1) | 提升 | 成本变化 |
|---|---|---|---|---|
| 简单问答 | 90% | 90% | 0 | ~4× |
| 代码生成 | 75% | 88% | +13% | ~4× |
| 架构设计 | 70% | 85% | +15% | ~4× |
| 复杂 bug 排查 | 60% | 80% | +20% | ~4× |
| 长文档总结 | 80% | 90% | +10% | ~4× |
| 数学推理 | 65% | 82% | +17% | ~4× |

**关键洞察**:任务越复杂,MoA 提升越明显 — 因为多模型可以**互相补足短处**。

## 6.8 进一步阅读

- [03-quickstart.md](03-quickstart.md) — 试玩台体验
- [04-api-reference.md](04-api-reference.md) — MoA API 完整字段
- [07-faq.md](07-faq.md) — 常见问题
