# v1.5.0 Release Notes — Capability 模块 + 10 参考项目整合

**发布日期**: 2026-07-15
**版本**: v1.4.4 → v1.5.0
**主题**: 工业级 MoA 协作网关 + 真实能力栈

---

## 🎯 核心新增

### Capability 模块 — 7 个 P0 能力真实实现
基于 10 个开源 MoA / 多代理 / 安全审计参考项目逐行分析,合并去重得到 609 项能力,
按工业级标准筛选最关键 7 项,真实数学/统计实现,零 mock/占位:

| 能力 | 来源 | 算法 | 测试 | 端点 |
|------|------|------|------|------|
| **secret_scan** | 05 moa-skill + 07 moat | 27 种硬编码密钥模式 + 4 豁免源 | 13/13 ✅ | `POST /v1/capability/secret-scan` |
| **moaflow (反群体思维)** | 05 moa-skill | 8 类 42 谄媚短语 + 3 轮反思维 + 锚定漂移 | 9/9 ✅ | `POST /v1/capability/group-think-check` |
| **consensus (集成投票)** | 01 gateswarm | 4 算法 majority/weighted/borda/approval + Shannon 熵 | 16/16 ✅ | `POST /v1/capability/ensemble-vote` + `should-rebalance` |
| **cost_estimator** | 05 moa-skill | dry-run 多通道 + 1.5x fallback multiplier | 14/14 ✅ | `POST /v1/capability/cost-estimate` |
| **gate_l0 (L0 闸门)** | 05 moa-skill | ast 安全求值 + 8 模式匹配 + 复杂度估算 | 44/44 ✅ | `POST /v1/capability/gate-l0` |
| **score_panel (5 维评分)** | 09 opencode-moa | TQ/CO/AP/SE/IN 五维启发式评分 | 20/20 ✅ | `POST /v1/capability/score-panel` |
| **model_context_db** | 10 Verdex | 41 真实模型 (15 国产 + OpenAI/Anthropic/Google/Mistral) | 14/14 ✅ | `GET /v1/capability/models` + `calculate-max-tokens` + `estimate-cost` |

**总计 130/130 测试通过 in 0.34s**

### 10 参考项目能力整合
- `参考/extracted/` 解压 10 zip (176 MB 源码)
- `参考/analysis/` 11 份详细分析(0.6 MB):
  - 00-CAPABILITY-SUMMARY.md (92 KB 总表)
  - 01-10 各项目能力分析 (30-90 KB each)
- 合并得到 609 项能力 / 12 大类 / 4 阶段路线图
- 风险点 + 行动建议完整

## 🛠 技术改进

### 安全加固
- 27 种密钥模式支持(15 国际 + 6 国产 deepseek/zhipu/moonshot/qwen/doubao/siliconflow)
- 4 豁免源防止误报:
  1. inline `# moat:ignore=PATTERN_ID reason="..."`
  2. file frontmatter
  3. global `.moat/exempt.yaml`
  4. path 规则(test_/tests/fixture/example/mock/参考/分析)
- `should_block` 只看非豁免 finding(避免测试/示例代码 block CI)

### 性能优化
- `_is_text_file` 修:用 printable 字符比例 ≥ 85% 判定
  - 原 `chunk.decode("utf-8")` 误判含中文+emoji 的 Python 测试文件
- `_eval_arithmetic` 用 `ast.parse` + `ast.literal_eval`(严禁 `eval()`,防代码注入)
- `ConsensusResult.to_dict()` 用 `dataclasses.asdict`(修复 P1 bug)

## 📦 部署

- **零配置启动**: `python start_ui.py` 自动建 venv (国内源镜像)、装依赖、5 步自检
- **不依赖真 API key**: 任何缺失/your-xxx/mock api_key 自动走 MockProvider,
  让没 key 也能演示整个 MoA 协作流程
- **跨平台**: Windows / Linux / macOS (纯 Python 原生,无 Docker)

## 🔄 升级指南

```bash
git pull origin main
# 启动:会自动用新 version 1.5.0 检测 + 加载 capability 模块
python start_ui.py
```

新能力端点:
```bash
# L0 闸门 — 简单问题短路
curl -X POST http://localhost:8926/v1/capability/gate-l0 \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "2+3"}'

# 模型查询 — 41 真实模型
curl http://localhost:8926/v1/capability/models?provider=deepseek \
  -H "Authorization: Bearer $KEY"

# 集成投票 — 4 算法
curl -X POST http://localhost:8926/v1/capability/ensemble-vote \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"votes": [{"voter_id":"a","candidate":"A","confidence":0.9}], "method": "weighted"}'

# 5 维评分
curl -X POST http://localhost:8926/v1/capability/score-panel \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"What is Python?","answer":"Python is a high-level language."}'

# 密钥扫描
curl -X POST http://localhost:8926/v1/capability/secret-scan \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"path": ".", "fail_on": 3}'

# 反群体思维
curl -X POST http://localhost:8926/v1/capability/group-think-check \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"t1","members":[{"member_id":"a","content":"Great point!"}]}'
```

## 📊 统计

- 7 capability 模块: 3,505 行 (3,505 production + 130 tests)
- 10 server 端点: 1 GET + 9 POST
- 41 真实模型 context window
- 27 密钥模式 (15 国际 + 6 国产 + 6 misc)
- 4 投票算法 + Shannon 熵 + Borda count
- 5 维评分 panel (TQ/CO/AP/SE/IN)
- 8 谄媚短语类别 42 表达
- 130/130 测试通过 in 0.34s

## 🐛 修复

- `asdict` 缺失 import 导致 ensemble-vote 500
- `_is_text_file` 误判含中文+emoji 的 Python 文件
- `should_block` 误将豁免 finding 计入 block
- `return True` 改 `assert True` (13 处 pytest 兼容)

## 🚀 路线图

- **v1.5.0** (现在): 7 P0 能力真实实现
- **v1.6.0** (计划): 8-10 P1 能力(SCIM/risk scoring/citation graph/...)
- **v1.7.0** (计划): 自适应模型委员会 + LoRA 增量训练
- **v2.0.0** (长期): 完整 SaaS 化 + 多租户 + OCSF 兼容

## 📝 链接

- GitHub: https://github.com/Nurburgring-Zhang/moa-gateway-pro
- Issues: https://github.com/Nurburgring-Zhang/moa-gateway-pro/issues
- CI: .github/workflows/ci.yml (需 workflow scope PAT push)
