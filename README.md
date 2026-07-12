# MoA Gateway Pro

[![CI](https://github.com/Nurburgring-Zhang/moa-gateway-pro/actions/workflows/ci.yml/badge.svg)](https://github.com/Nurburgring-Zhang/moa-gateway-pro/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

> 工业级多模型协作网关 — 智能 auth-fallback、桌面 UI(基于 flet)、Together AI MoA 论文对齐、4 国产预设、内置 Benchmark Suite、端到端可跑。

## 特性

- **MoA 引擎 9 种 strategy**: `parallel` / `compose` / `judge` / `chain` / `pipeline` / `single` / `layered`(论文 §2.2 多层) / `single_proposer`(论文 §3.3) / `ranker`(论文 Figure 4)
- **12 preset**: 6 通用(`fast` / `balanced` / `quality` / `pipeline` / `chinese_battalion_layered` / ...) + 4 国产(`chinese_battalion` 等) + 1 评分器 + 1 单一采样
- **12 endpoint**: deepseek / zhipu / moonshot / qwen / doubao / baichuan / lingyi / siliconflow / openai / anthropic / mistral / openrouter
- **智能 auth-fallback**: 401 / 403 / startup timeout 自动切 MockProvider(无需真 key 也能跑完整 MoA)
- **Mock Provider**: 6 类智能模拟回答(代码 / 中文 / 数学 / 翻译 / 创意 / 通用)
- **桌面 UI(flet)**: 6 page + 三主题(Light / Dark / Auto)+ 5 步自愈启动器
- **WebUI 备选**: 单文件 SPA(89KB),远程 / 浏览器访问
- **8 个 server 端点**: `/v1/moa/{execute, eval, presets, similarity, flask, benchmark, cost-pareto, prompts}`
- **Benchmark Suite**: 11 题 / 5 类(reasoning / code / chinese / creative / professional)+ Pareto 分析 + FLASK 12 维评分
- **自愈启动**: `start.py` 自动创建 venv / 多源 pip 镜像 / 依赖检测 / watchdog
- **深度审核**: 5 轮审计发现 95 项问题 + 7 类 UI bug 全修,见 [docs/AUDIT-REPORT.md](docs/AUDIT-REPORT.md)

## 快速开始

### 安装

```bash
git clone https://github.com/Nurburgring-Zhang/moa-gateway-pro.git
cd moa-gateway-pro
pip install -r requirements.txt
```

### 配置管理员密码(**首次启动必须设置**)

为安全起见,`config.yaml` 的 `auth.admin_password` 默认是空,**必须**通过环境变量或编辑 yaml 设置:

```bash
# 方式 1: 环境变量(推荐)
export MOA_ADMIN_PASSWORD='YourStrong#Pass1'    # Linux/macOS
$env:MOA_ADMIN_PASSWORD = 'YourStrong#Pass1'    # PowerShell

# 方式 2: 编辑 config.yaml
#   auth:
#     admin_password: 'YourStrong#Pass1'
```

### 启动桌面 UI(自愈式启动器,跨平台)

```bash
python start_ui.py
```

启动器会:
1. 检测虚拟环境(不存在自动创建)
2. 切换到 venv Python
3. 检查/安装依赖(清华源镜像)
4. 验证 UI 模块可加载
5. 启动 flet 桌面 UI

### 启动 server(纯命令行)

```bash
export MOA_ADMIN_PASSWORD='YourStrong#Pass1'
python -m uvicorn moa_gateway.server:app --host 0.0.0.0 --port 8910
```

### 第一次使用

```bash
# 1. 登录拿 token
curl -X POST http://127.0.0.1:8910/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourStrong#Pass1"}'
# → {"token": "eyJ..."}

# 2. 创建一个 API key(给客户端用)
curl -X POST http://127.0.0.1:8910/api/api-keys \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"name":"my-client","quota_rpm":60,"quota_daily_tokens":1000000}'

# 3. 调 MoA
curl -X POST http://127.0.0.1:8910/v1/chat/completions \
  -H "Authorization: Bearer mgw-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "moa",
    "messages": [{"role": "user", "content": "用 Python 写一个 LRU Cache"}],
    "preset": "chinese_battalion"
  }'
```

## 文档

- [01 概览](docs/01-overview.md) — 项目介绍与设计目标
- [02 架构](docs/02-architecture.md) — 系统架构、模块依赖、数据流
- [03 快速开始](docs/03-quickstart.md) — 详细安装 / 配置 / 第一次使用
- [04 API 参考](docs/04-api-reference.md) — 全部 server 端点 + 签名
- [05 Agent 集成](docs/05-agent-integration.md) — OpenClaw / LangChain / Dify 接入
- [06 MoA 深入](docs/06-moa-deep-dive.md) — 9 种 strategy 实现细节
- [07 FAQ](docs/07-faq.md) — 常见问题(启动 / 鉴权 / 性能 / 安全)
- [08 UI 架构](docs/08-ui-architecture.md) — 桌面 UI 架构
- [09 Auth Auto-Fallback](docs/09-auth-auto-fallback.md) — 智能 fallback 机制
- [AUDIT-REPORT](docs/AUDIT-REPORT.md) — 5 轮审计报告(95 项)

## 开发

```bash
# 跑测试
python -m pytest tests/ -v

# 跑端到端 UI 审核(headless,模拟用户操作流程)
python scripts/audit_ui_e2e.py

# 跑 4 preset 端到端
python scripts/test_4_presets.py
```

## 许可

MIT License — 详见 [LICENSE](LICENSE)

## 致谢

- [Together AI MoA 论文](https://arxiv.org/abs/2406.04692) — Multi-layer / Single-proposer / Ranker 灵感
- [Hermes v0.18](https://github.com) — 显式 reference_models 风格
- [OpenSquilla v0.5.0](https://github.com) — 4 国产 + 1 聚合 风格
- [flet](https://flet.dev) — Flutter for Python,自带 Skia 渲染