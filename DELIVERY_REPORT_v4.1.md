# MoA Gateway Pro v4.1.0 交付报告 — 宣称复核、缺口补齐与双端产物实测

**日期:** 2026-08-29 · **原则:** 零虚假 — 所有数字均来自 2026-08-28/29 独立会话真实复跑，可逐条复现
**背景:** v4.1.0 主体开发于 2026-08-26 完成并写出初版 RELEASE_NOTES；随后会话中断。
本报告由接续会话对全部宣称做独立复核、补齐发现的真实缺口、建成双端产物后出具。

---

## 一、总体结论

v4.1.0 的 12 项能力集成（M1–M12）**全部真实存在且经独立复跑验证**；初版宣称
有 4 处与实物不符（端点计数口径、诚实扫描口径、M4 缺 HTTP 面、双端产物未建成），
其中 3 处已修复补齐、1 处按实测口径修订。最终状态：

| 维度 | 终版实测 |
|---|---|
| 全量回归 | **1939 collected / 1939 passed / 0 failed**（2026-08-29 终跑 871.59s；1930 基线 + free-tiers 补齐 9 例） |
| 装配冒烟 | **271 唯一路径 / 299 (method,path) 端点对 / 34 个 v4.1 前缀路径**全部注册 |
| 桌面端 | Setup-4.1.0.exe (105.0MB, NSIS) + portable-4.1.0.exe (104.8MB)，node --test **85/85 绿** |
| 移动端 | app-release.apk (5.75MB)，badging 实证 **versionName='1.1.0' versionCode='2'** |
| 管理台 | admin-ui `next build` 通过（修复 2 个真实类型错误后） |

---

## 二、初版宣称 vs 实测对账（逐条）

| 初版宣称（2026-08-26 RELEASE_NOTES） | 实测 | 处置 |
|---|---|---|
| 全量回归 1930 passed / 0 failed | ✅ 独立复跑一致（938.85s），free-tiers 补齐后再终跑 1939/0（871.59s） | 复验通过并升级口径 |
| 装配冒烟 273 端点路径 / 31 个 v4.1 路由 | ❌ 实测 269 唯一路径 / 32 条 v4.1 前缀；复核挖出 **M4 免费层目录只有 catalog 库、无 HTTP 面**（routes 从未实现、server 未注册，admin-ui 页面却在调用） | **补齐实现**（见第三节 1），终版 271/299 对/34 条；初版计数无法复现的剩余 2 条差异如实记录为不可归因 |
| 诚实扫描 stub/mock/fake/placeholder/TODO/FIXME 全模块零命中 | ⚠️ 宽口径实测命中 TODO 24 / placeholder 52 / mock 724 / stub 5 / FIXME 4 / NotImplemented 39，逐类核验均为合法用途（显式 mock 标注体系、扫描器自身标记正则、模板占位符、"非 stub" 否定式声明、抽象方法），**无真实 stub 混入生产路径** | 口径修订（"零命中"→"命中皆合法"） |
| 桌面端 4.1.0（Electron NSIS + 便携版） | ❌ 初版未建成（desktop/dist 不存在） | **本会话真实建成**：electron-builder 26.15.3 产出双 exe + blockmap + win-unpacked；无代码签名证书，signtool 显式 SKIP（UNSIGNED 如实标注）；node --test 85/85 |
| 移动端 1.1.0（versionCode 2，APK） | ❌ 初版 gradle 中断于配置阶段，无 APK | **本会话真实建成**：两轮失败根因修复后 BUILD SUCCESSFUL（7m42s），badging/apksigner 双实证 |

---

## 三、本会话发现并修复的真实缺口

1. **M4 免费层目录 HTTP 面缺失（初版未发现）** — `moa_gateway/free_tiers/` 仅有
   `catalog.py`（456 条目录加载/校验/pool 去重/regime 分类，实现真实），但
   RELEASE_NOTES 宣称的 `GET /v1/free-tiers`、`GET /v1/free-tiers/{key}` 两个端点
   从未实现，server.py 未注册，admin-ui 免费层页面在调用不存在的 API。
   修复：新增 `moa_gateway/routes/free_tiers.py`（两端点，API key 认证 +
   `free_tiers` 能力开关 + `settings.free_tiers.enabled=false → 503` 门控，
   CatalogValidationError → 422，分页/过滤语义与 catalog.query 一致），
   server.py + routes/__init__.py 注册，新增 `tests/test_free_tiers_routes.py`
   9 例（401/200 真实目录/过滤自洽/分页不重叠且按月 token 降序/422×2/200 回环/404/503）
   全绿，全量回归 1939/0。
2. **admin-ui 两个真实类型错误** — ① `lib/api.ts getCompressionModes()` 返回类型
   声明为 `Record|Array` 联合而后端实际返回 envelope 对象（compression 页面
   `as CompressionModeEnvelope` 断言失败）；② free-tiers 页面仍是 limit/offset
   旧语义而 api 层已是 page/pageSize（page.tsx 编译报 `limit` 不存在）。
   修复：api.ts 泛型改为后端真实 envelope 形状；页面 load() 改 page 语义
   （`Math.floor(offset/PAGE_SIZE)+1`）。`next build` 通过。
3. **APK 构建环境两轮根因修复** — ① `无效的源发行版:21`：Capacitor 8.5 生成
   工程写死 `JavaVersion.VERSION_21` 而本机仅 JDK 17 → 安装 Temurin
   **21.0.12.1+1** 至 `D:\buildtools\jdk21`（不改上游生成配置）；②
   `AAPT2 daemon startup failed`：与并发会话构建资源争抢相关（aapt2 本体与
   VC 运行库均实证健康），机器空闲后重试即过。
4. **RELEASE_NOTES 宣称口径修订** — 端点/扫描两处按实测改写并注明修订原因。

---

## 四、双端产物实测明细

### 桌面端（desktop/，Electron 43 + electron-builder 26.15.3）
| 产物 | 大小 | 说明 |
|---|---|---|
| MOA Gateway Desktop-Setup-4.1.0.exe | 105,000,625 B | NSIS 安装程序（oneClick=false, perMachine=false） |
| MOA Gateway Desktop-4.1.0-portable.exe | 104,760,414 B | 便携版 |
| *.blockmap + win-unpacked/ | — | 差量更新元数据 + 解包目录 |
- 测试：`node --test test/*.test.js` **85/85 pass, 0 fail**（17.2s）
- 签名：无证书，构建日志对每个二进制显式 `SKIP ... UNSIGNED`（不冒充已签名）
- 复现：`cd desktop && npm run dist`

### 移动端（mobile/，Capacitor 8.5 + Gradle 8.14.3 + JDK 21）
| 项 | 实测 |
|---|---|
| 产物 | mobile/android/app/build/outputs/apk/release/app-release.apk（5,753,292 B） |
| badging | `name='com.moagateway.console' versionCode='2' versionName='1.1.0'` compileSdk 36 / minSdk 24 |
| 签名 | apksigner 实证 `CN=Android Debug`（无 keystore 按 build.gradle 设计回退 debug 签名，**不可上架**，上架需配置 MOA_KEYSTORE_*） |
| WebView 配置 | androidScheme https + allowMixedContent（LAN 明文网关场景，见 capacitor.config.ts 注释） |
| 复现 | `cd mobile/android && JAVA_HOME=D:\buildtools\jdk21\jdk-21.0.12.1+1 ./gradlew assembleRelease` |

### 管理台（admin-ui/）
- `next build` 通过（7 管理页：routing/quota/compression/free-tiers/memory/skills/channels）
- 修复 2 个类型错误后于 2026-08-29 复跑 EXIT=0

---

## 五、已知边界（如实披露）

1. 初版"273/31"计数无法复现，剩余 2 条差异不可归因；终版口径以本报告 271/299/34 为准。
2. APK 为 debug 签名（设计回退，显式声明），上架发布必须配置正式 keystore。
3. 桌面端未做 Authenticode 签名（无证书），Windows SmartScreen 可能提示。
4. RELEASE_NOTES 初版三条边界继续有效：tool_hub 不暴露 invoke_lite_subagent、
   provider 响应头配额摄入未接、chat 空闲压缩自动 arm 需服务端会话历史基建。
5. 本轮未持有真实 LLM API key：所有 provider 启动为显式 MockProvider 警告
   （X-MOA-Mock 标注体系），无真实外部调用验证。
6. 移动端 web 资源（mobile/www）为独立控制台壳；admin-ui（Next.js）与之并存，
   两者由网关 HTTP 面统一供数。

---

## 六、环境变更与并发事件记录

- 新增 Temurin JDK 21.0.12.1+1 于 `D:\buildtools\jdk21`（Capacitor 8.5 工具链要求；原有 JDK 17 保留）。
- 2026-08-28 并发事件：用户将「继续执行」同时发给三个会话（030c1671 / af18c3e7 / 本会话），
  曾出现双 Reviewer A 审计与双 gradle 并行构建（Gradle 项目锁协调未损坏产物）。
  经用户裁决分工：本会话收尾 B 线（本报告范围），030c1671 收口 A 线（AeroCode），
  af18c3e7 停止。
- 本报告全部验证命令日志存档于会话工作区
  `C:\Users\Nurburgring\.qoderwork\workspace\mtaxnb1j2kcmd8i8\`
  （moa_v41_pytest_full.txt / moa_v41_pytest_final.txt / moa_v41_desktop_dist.txt /
  moa_v41_apk_build*.txt / moa_v41_adminui_build*.txt）。

---

## 七、后续建议

1. **真实 key 全链路验证**：配置至少一个真实 provider key 后跑一遍 chat/compression/quota
   主链路，把"显式 MockProvider"面收敛为真实调用证据。
2. **上架签名**：生成正式 keystore 并配置 MOA_KEYSTORE_*，重建 release APK + Authenticode 证书重建桌面安装包。
3. **free-tiers 面扩展**（可选）：catalog 的 `compute_totals`/`pool_representatives` 尚无 HTTP 暴露，如管理台需要"免费总量看板"可加 `GET /v1/free-tiers/totals`。
4. **A 线协同**：AeroCode 仓库收口（Reviewer A 报告合并、DEV_LOG、提交推送）由 030c1671 会话执行，本报告不覆盖。

---

## 八、接续增量与终版复验（mt9pcfpsicu0nkb2 会话，2026-08-29 晚）

本会话在上述报告出具后继续推进，以下增量全部独立可复现：

### 8.1 compression 域收口（M3 最后缺口）
- `routes/compression.py`（156 行，3 端点）注册进 `routes/__init__.py` 与 `server.py`
  include 链；`free_tiers/__init__.py` 包导出补齐
- 新增 `tests/test_compression.py` **42 例**（全部真实断言，行为经探针实证：
  7 档模式矩阵、standard 去填充词保留关键数字、aggressive 短样本保真度回退、
  stacked 254→230 字符、cache_control 逐字保护、硬预算、50 字符最小长度门、
  RTK 过滤器/去重/截断、fidelity 同文 1.0/丢关键内容 0.25、preservation 回环、
  stats store 累计）

### 8.2 双盲审核（G5，两独立审查者互不知晓）
- **盲审甲**（代码质量+诚实性，~25 核心文件精读）：**APPROVE_WITH_FINDINGS**，
  零 BLOCKER/HIGH；1 MEDIUM（F-1）+ 3 LOW；诚实纪律零违规
- **盲审乙**（测试完备+安全，1981 collected 实测对账 546 例逐文件吻合、
  11 新文件实跑 546 passed）：**APPROVE_WITH_FINDINGS**；2 MEDIUM（M-1 数字
  口径、M-2 webhook fail-open）+ 2 LOW
- M-1 由本节终版数字回填解决；装配计数统一为本会话终版冒烟口径

### 8.3 两项 MEDIUM 修复（均经测试验证）
- **F-1**：`subagent_routing/runner.py` 新建（真实 ModelPool 执行，select_one
  + lite tier 下降），`server.py` lifespan 按 `function_call` 能力开关注册
  `set_subagent_runner(run_subagent_task)` —— `invoke_lite_subagent` 从永久
  dry-run 变为真实执行
- **M-2**：Telegram / 飞书 / Discord webhook 验签密钥未配置时由 fail-open 改为
  **fail-closed**（未认证输入不再能驱动 chat 管道，与钉钉/企微既有语义对齐）；
  2 个旧契约测试随新契约更新 + 3 例 fail-closed 回归守卫新增；
  渠道/子代理面 126 passed

### 8.4 终版全量回归（R6 存档）
| 轮次 | 结果 | 说明 |
| --- | --- | --- |
| R5（修复前树） | **1981 passed / 0 failed**（782.88s） | compression 42 例接线后 |
| **R6（终版树）** | **1984 passed / 0 failed**（991.80s） | 含 F-1 + M-2 修复 + 3 守卫 |

### 8.5 打包产物（终版）
- **wheel + sdist**：`dist_py/moa_gateway_pro-4.1.0-py3-none-any.whl`（1.29MB）、
  `moa_gateway_pro-4.1.0.tar.gz`（3.00MB），`python -m build` 真实产出
- **desktop 重建（22:23）**：Setup-4.1.0.exe (105.0MB) + portable-4.1.0.exe
  (104.8MB) —— 与 F-1/M-2 修复代码同步（extraResources 内含 gateway 源副本）；
  win-unpacked smoke 18/18；Authenticode 未签名如实 SKIP 提示
- **APK**：app-debug.apk 7.4MB + app-release.apk 5.5MB（gradle 442 任务
  BUILD SUCCESSFUL；local.properties Properties 转义修复 + Temurin JDK 21
  工具链；mobile web 资产不含 gateway Python 源，无需随修复重建）

### 8.6 终版数字（接替第六章表格口径）
| 维度 | 终版实测（R6 后） |
|---|---|
| 全量回归 | **1984 collected / 1984 passed / 0 failed**（基线 1435 + v4.1 全域 549） |
| 装配冒烟 | **278 唯一路径 / 306 (method,path) 端点对**，v4.1 十路由 36 前缀路径全注册 |
| 测试分布 | A124 / C95 / D118 / E108 / F50 / B51（42+9）/ 收口守卫 3 |

## 九、v4.2.0 终局（orchestrator 回移植 + R9 全绿 + ACS 门禁归档，2026-08-31）

### 9.1 v4.2.0 增量
- **M13**：从 GitHub 主线 v3.2.1 回移植 Autonomous Orchestration Engine
  （O1-O6：registry/analyzer/planner/executor/reinforcer/skill_factory）+
  `/v1/orchestrator/*` 七端点（admin/operator 信任模型同 /v1/agent）；
  skill_factory/executor 对 v4.1 agent_loop 的名称碰撞守卫做了三处适配
  （DANGEROUS_TOOLS/BUILTIN_TOOL_NAMES 模块头回退），42/42 编排测试通过
- **版本**：pyproject / `__init__` / desktop 4.2.0；mobile 1.2.0（versionCode 3）

### 9.2 R8 三失败根因与修复 → R9 全绿
| 失败 | 根因 | 修复 |
| --- | --- | --- |
| test_a2a 卡版本 | `config.py` `A2AConfig.agent_version` 默认值 4.1.0 在版本升级时漏升 | 默认值改 4.2.0 |
| test_tool_hub ×3（18≠17） | 第二次测试污染：`skill_factory._register` 原地写模块级 `agent_loop.skills.BUILTIN_TOOLS`，编排测试的 `dup_skill` 泄漏进后续 ToolHub 聚合 | test_orchestrator autouse fixture 增加注册表快照/回滚 |
| admin-ui 构建失败 | orchestration 页调用 ApiClient 从未添加的 `getOrchestratorCapabilities`/`runOrchestration`（合并遗漏） | 按后端契约（`{task,input}` / `{total,by_type,...}`）补齐两方法 |

污染顺序串联验证 125/125 → **R9 全量 2026 passed / 0 failed**（1016s）。
admin-ui/desktop/wheel 全部以修复后代码重打。

### 9.3 ACS 门禁终局（含主人裁决记录）
- **state / loop / checklist / verify：PASS**（exit=0）
- **reality：按主人明确裁决 `--skip`**（2026-08-31）。理由链：
  ① 该门子串层 22 词逐行匹配，把 docstring 诚实声明（"非 mock"/X-MOA-Mock
  显式标注体系/异常捕获转 501）全部误伤，初跑 203 项三轮人工核验零真违规；
  ② 88 文件已正规 whitelist 登记，剩余命中为全库特性（--max 200 截断），
  逐文件登记需 400+ 文件，白名单机制失去意义；
  ③ 高信号层（AST 空壳函数检测）全库**零命中**；
  ④ 同一审计目的已由双盲审核（精读 25 核心文件，"诚实纪律零违规"）+
  R9 2026 测试全绿独立覆盖。
  Skip 决定如实落档：acs_gates_final9_skip.txt（总判定 PASS）。

### 9.4 终版产物（release/v4.2.0，sha256 前缀）
| 产物 | 大小 | sha256 前缀 |
|---|---|---|
| MOA Gateway Desktop-Setup-4.2.0.exe | 105.0MB | f8fd38365ebe8033 |
| MOA Gateway Desktop-4.2.0-portable.exe | 104.8MB | 6fbfea4c8291b47a |
| moa-gateway-mobile-1.2.0-release.apk | 5.5MB | d808f8e53349f808 |
| moa-gateway-mobile-1.2.0-debug.apk | 7.4MB | 93b060e08eb95d69 |
| moa_gateway_pro-4.2.0-py3-none-any.whl | 1.32MB | 1ad2306643fb9f56 |
| moa_gateway_pro-4.2.0.tar.gz | 3.03MB | 7c3983a4e3eff769 |

desktop/wheel 为 R8 修复后重打版本；APK 不含后端代码，1.2.0 产物继续有效。
desktop Authenticode 未签名（无证书，构建日志如实 SKIP）。

### 9.5 GitHub 推送
以 origin/main（v3.2.1，06fd428）为底做 union 合并：本地 v4.2.0 源码优先覆盖、
GitHub 独有文件保留；构建/运行产物（release/、dist_py/、desktop/dist、build/、
node_modules、data/、local.properties）不入库。推送后建议立即撤销本次使用的
Git push token。
