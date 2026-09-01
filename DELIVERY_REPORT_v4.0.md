# MoA Gateway Pro v4.0.0 交付报告 — v3.1.1 宣称 vs v4.0.0 实测

**日期:** 2026-08-21 · **原则:** 零虚假（G3/G4）— 所有数字均可用仓库内脚本复现

## 一、数字对账（宣称 vs 实测）

| 项目 | v3.1.1 宣称 | v4.0.0 实测 | 结论 |
|---|---|---|---|
| API 端点数 | 141（README 头条） | **262 个 (method,path) 端点 / 237 条唯一路由**（GET 86 / POST 156 / DELETE 12 / PUT 8） | 原数字严重低估。根因：FastAPI 0.139 把 30 个 router 包成 `_IncludedRouter` 惰性对象，顶层 `app.routes` 只能看到 4 个文档路由；穿透 `original_router` 逐条清点才是真值。复现：`count_endpoints.py`，清单 `endpoint_list.txt` |
| 测试用例数 | 236（README 头条） | **1435 collected / 1435 passed / 0 failed** | 原数字系早期口径。v3.1.1 实际基线即 1071 passed（其 release notes 自己写的）；v4.0.0 净增 364 例 |
| 回归状态 | 1071 passed, 0 failed | **1435 passed, 0 failed**（两次独立干净重跑：555.25s 与发版态终验） | 全部最新修复（含版本统一改动）都在回归覆盖内 |
| 版本号一致性 | 四处硬编码 3.1.1（`__init__`/pyproject/openapi/health） | **单一真源** `moa_gateway.__version__`，openapi 与 /health 动态引用；实测均 4.0.0 | 从"四处手动同步"升级为结构性防漂移 |

## 二、P1–P11 计划缺口交付对账

v3.1.1 之后的开发状态评估列出 11 项缺口，v4.0.0 全部真实落地（无 mock 顶替、无占位实现）：

| # | 缺口 | 交付物 | 验证方式 |
|---|---|---|---|
| P1 | SSRF 编码 IP 绕过（v3.1.1 遗留） | `url_validator.py` 平台无关 inet_aton 归一化 + ::/96 + 前导零八双解 fail-closed | 25 个新拦截/放行用例进回归 |
| P2 | 多媒体 provider 接线（图像空 key、音乐、ASR/TTS、Kling） | routes/music.py、video.py Kling、audio.py provider 参数、.env.example 补全 | test_multimodal_wiring.py 42 例 |
| P3 | MultiModalFanout 多路并发聚合引擎 | all/fastest/best 三模式，逐路 provider/latency/cost/mock 标注 | 回归覆盖 + 收尾加固（见下） |
| P4 | ToolHub 统一工具中枢（P4-4 moa_graph workflow） | agent_loop/MoA tool loop 全接入，guardrails 统一 | 回归覆盖 |
| P5 | MCP 完整化（stdio 外连、SSE 投递、外部工具并入、双 server 合并） | 统一 RBAC + 命名空间 | 回归覆盖 |
| P6 | CLI 真实化（外部 CLI 注册表 + subprocess） | capability/cli_registry.py 三通道真实执行 | test_cli_registry.py |
| P7 | 多 AI 同框对话（Dialogue Rooms） | dialogue/ 新模块，三编排模式，SQLite WAL 持久化，SSE + 回放 | test_dialogue.py 46 例 + **P7-4 真实冒烟 6/6** |
| P8 | 主动任务分析（LLM 分解→路由→多路执行→自愈闭环） | TaskAnalyzer/CapabilityRouter/TaskSupervisor | test_task_pipeline.py 18 例（含新增 2） |
| P9 | Windows 桌面端 | desktop/（Electron + electron-builder，NSIS/portable，版本 4.0.0） | 419 源文件逐件核验；安装包需本机工具链构建（诚实标注，不造假二进制） |
| P10 | Android 端 | mobile/（Capacitor 完整 Gradle 工程，v1.0.0） | 83 源文件逐件核验；APK 构建同上 |
| P11 | 回归 + 打包 | 本报告 + release 目录三产物 | 见第四节 |

## 三、收尾对抗复审：新发现并修复的 4 处真实缺陷

发版前不派子代理、直接走查源码，挖出 4 处 v3.1.1 审计未覆盖的真实 bug（全部实测复现后修复、全部有回归测试或冒烟证据）：

1. **CLI 入口路由 bug** — `python -m moa_gateway --port 8088` 把 `--port` 当子命令，报 `invalid choice`（实测复现）。修复：`__main__.py` 首参判别，非已知子命令按 serve 解析。新增 2 测试。
2. **多模态全失败证据丢失** — fanout 全败抛裸 `RuntimeError`，逐平台失败证据被丢，supervisor 自愈对永久坏平台无效空转。修复：`MultimodalAllFailedError` 携带 FanoutResult；自愈剔除 no_key/skipped_mock_unavailable 平台后重试，无可重试则 failed 终态留证。新增 2 测试。
3. **新端点缺 per-key 限流** — routes/multimodal.py、routes/task_pipeline.py 补齐 `check_and_incr`，与 chat/moa/dialogue 一致。
4. **WebUI 重复 escapeHtml** — index.html 两个同名函数，行 1451 版本不转义引号（XSS 面）。删除之，保留完整版；node --check 通过。

## 四、打包产物与安装验证（全部实测）

release 目录 `moa-gateway-pro-v4.0.0-release/`：

| 产物 | 大小 | 内容 |
|---|---|---|
| moa_gateway_pro-4.0.0-py3-none-any.whl | 1.0 MB | 312 条目；数据文件 43（prompts 30 / builtin workflows 5 / webui 1 / param_templates 3 / migrations 4）与 v3.1.1 口径一致；entry_points 三命令齐全 |
| moa_gateway_pro-4.0.0.tar.gz（sdist） | 1.9 MB | 597 条目；含 docs/desktop/mobile/tests，零 junk（无 node_modules/pyc） |
| moa-gateway-pro-v4.0.0-source.tar.gz | 2.1 MB | 619 文件全仓快照（剔除会话 scratch），对齐 v3.1.1 source 包标准 |
| RELEASE_NOTES_v4.0.md / 本报告 | — | 发版说明 + 交付对账 |

**安装级验证（非源码级）**：全新 venv `pip install` wheel → 成功；`import moa_gateway` → 4.0.0；site-packages 数据文件全在（webui 103412 字节完整）；TestClient 实启 → `/openapi.json` 200、info.version 4.0.0、237 paths（与源码实测逐一吻合）；`/health` 200、version 4.0.0。

## 五、诚实性声明（不粉饰项）

- desktop/mobile 交付的是**完整可构建源码工程**；NSIS 安装包与 APK 二进制未在本审计环境产出（缺 Android SDK/构建链），不做假构建产物。
- 无真实 provider key 时，多模态/对话/任务管线按 D6 政策返回**显式标注**的合成结果；配 key 后走真实 provider。失败即失败（error/timeout/no_key），绝不伪造成功。
- README 旧头条数字（141 端点/236 用例）属口径过时的低估而非造假，v4.0.0 以可复现脚本实测更正并写进 release notes。

## 六、复现命令

```bash
# 端点实测
python count_endpoints.py            # → 237 路径 / 262 端点对
# 全量回归
python -m pytest tests/ -q           # → 1435 passed, 0 failed
# 打包
python -m build --no-isolation       # → dist/*.whl + *.tar.gz
# 安装验证
python -m venv v && v/Scripts/pip install dist/moa_gateway_pro-4.0.0-py3-none-any.whl
```
