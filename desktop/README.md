# MOA Gateway Desktop

MOA Gateway 的 Windows 桌面客户端（Electron）。它把本仓库的 Python FastAPI
网关（`moa_gateway.server:app`）作为受管子进程运行，并提供一个本地深色控制台：

- 拉起 / 停止 / 崩溃自动重启（指数退避，次数封顶）网关进程
- 轮询 `GET /health/ready` 做健康监控（运行中降级检测、启动就绪超时）
- 端口冲突自愈：首选端口被占用时自动向上扫描空闲端口
- 服务日志环形缓冲（行数 + 字节双上限）、实时流式推送到 UI、一键导出
- 系统托盘图标（显示/启动/停止/退出）、最小化进托盘、开机自启
- API Key 用 Electron `safeStorage`（Windows 下即 DPAPI）加密后落盘，启动时以环境变量注入网关进程
- 服务就绪前显示带实时启动日志的等待页；就绪后在「控制台」视图内嵌网关 WebUI（`GET /`）

> 默认端口 **8910**（与 `moa_gateway/config.py` 的 `ServerConfig.port` 一致）。
> 桌面端通过网关文档化的环境变量覆盖 `MOA_HOST` / `MOA_PORT` 注入实际端口。

---

## 目录结构

```
desktop/
├── main.js                     # Electron 主进程入口（窗口/托盘/单实例/装配）
├── preload.js                  # contextBridge 唯一 IPC 桥（contextIsolation 开启，nodeIntegration 关闭）
├── electron-builder.yml        # 打包配置（NSIS + portable 双目标）
├── package.json
├── assets/                     # icon.ico / icon.png / tray.png
├── scripts/
│   ├── generate-icons.js       # 从 SVG 生成 ico/png 图标（无第三方依赖）
│   ├── sign.js                 # Authenticode 签名钩子（无证书时显式产出未签名包）
│   └── syntax-check.js         # 对全部 JS 跑 node --check
├── src/
│   ├── main/                   # 主进程模块（纯逻辑模块不依赖 Electron，可单测）
│   │   ├── gateway-manager.js  # 子进程生命周期状态机 + 健康轮询 + 退避重启 + 进程树终止
│   │   ├── port-probe.js       # TCP 端口探测（bind + connect 双相校验，Windows 特化）
│   │   ├── config-store.js     # 桌面端配置持久化（原子写、损坏隔离、字段校验）
│   │   ├── log-store.js        # 日志捕获（跨块行切分）+ 导出
│   │   ├── ring-buffer.js      # 定容日志环形缓冲（行数/字节双上限）
│   │   ├── backoff.js          # 确定性指数退避策略（1s→2s→4s→…→30s 封顶，8 次封顶）
│   │   ├── secret-store.js     # safeStorage(DPAPI) 加密的 API Key 存储
│   │   ├── paths.js            # 网关仓库 / Python 解释器自动发现
│   │   ├── static-server.js    # 回环静态服务器（渲染壳经 http://127.0.0.1 提供，避免混合内容）
│   │   ├── tray.js             # 托盘菜单
│   │   ├── ipc.js              # 全部 ipcMain handler
│   │   └── smoke.js            # `--smoke-test` 无窗口自检
│   └── renderer/               # 渲染层（原生 HTML/CSS/JS，无构建步骤）
│       ├── index.html          # 控制台视图 + 服务管理视图
│       ├── app.js
│       └── styles.css
└── test/                       # node --test 单测（85 个用例）+ fake-gateway 夹具
```

---

## 环境要求

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| Node.js | ≥ 22（x64） | 开发与打包 |
| npm | 随 Node 附带 | |
| Python | 3.10+，且已安装网关依赖 | 运行网关服务的先决条件 |
| 网关依赖 | `pip install -r requirements.txt` | uvicorn / fastapi 等 |
| 操作系统 | Windows 10/11 x64 | 打包目标；开发亦在此验证 |

Python 解释器按以下顺序自动发现（也可在「服务管理 → 服务配置」手工指定）：

1. 用户配置的 `gateway.pythonPath`
2. 仓库上级/内部的 `venv/Scripts/python.exe`、`.venv/Scripts/python.exe`
3. 系统 `python`（PATH）、`py` 启动器

网关仓库按以下顺序发现：

1. 用户配置的 `gateway.repoPath`
2. 打包产物内置副本：`resources/gateway`（electron-builder `extraResources` 注入）
3. 开发布局：`desktop/` 的上一级目录（即本仓库根）
4. 工作目录的上一级目录

---

## 开发运行

```powershell
cd desktop
npm install          # 安装 electron / electron-builder
npm start            # 启动桌面端（自动探测仓库与 Python，默认自动拉起网关）
```

首次启动若网关依赖未装，等待页会展示真实错误与日志；在「服务管理」里配置
Python 路径后点「启动」即可。

### 无窗口自检（CI / 无桌面会话）

```powershell
npm run smoke        # electron . --smoke-test
```

冒烟自检真实执行以下项目并打印 `[SMOKE] PASS/FAIL`：配置读写与损坏回退、
环形缓冲上限、退避序列、真实端口占用探测、日志切分与导出、**真实 DPAPI
加密回环**、回环静态服务与路径穿越拦截、仓库/Python 发现。

---

## 测试与静态检查

```powershell
npm test             # node --test：85 个用例（含真实子进程集成测试）
npm run check        # 对全部 28 个 JS 文件执行 node --check
```

单测说明：

- 纯逻辑模块（`port-probe` / `config-store` / `ring-buffer` / `backoff` /
  `log-store` / `secret-store` / `static-server` / `paths`）全部以真实
  I/O 测试（真实 socket、真实文件、真实子进程），无 mock 框架。
- `gateway-manager.test.js` 通过 `test/fixtures/fake-gateway.js`（一个真实
  HTTP 进程，遵守 `MOA_HOST`/`MOA_PORT` 与 `/health/ready` 契约，支持故障
  注入）驱动完整状态机：启动→就绪→停止、端口占用自愈、瞬时崩溃自动重启、
  持续崩溃耗尽退避、就绪超时杀进程、backoff 期间停止取消重启等。

---

## 构建与打包

```powershell
npm run dist             # 同时产出 NSIS 安装包 + 便携版（x64）
npm run dist:nsis        # 仅 NSIS 安装包
npm run dist:portable    # 仅便携版 exe
npm run dist:dir         # 仅打目录（调试打包问题用）
```

产物输出到 `desktop/dist/`：

| 产物 | 文件名模板 |
| --- | --- |
| NSIS 安装包 | `MOA Gateway Desktop-Setup-<version>.exe` |
| 便携版 | `MOA Gateway Desktop-<version>-portable.exe` |

打包要点（见 `electron-builder.yml`）：

- `appId: com.moagateway.desktop`，版本取自 `package.json`
- 安装包同时内置网关源码只读副本（`extraResources`：`moa_gateway/`、
  `config.yaml`、`requirements.txt` → `resources/gateway/`），装完即可定位
  网关；**Python 解释器仍是先决条件**（客户端自动探测 venv 或用户指定路径）
- NSIS：非 one-click、允许选择安装目录、桌面/开始菜单快捷方式、卸载保留用户数据
- asar 打包；测试/脚本/dist 不进入安装包

### 代码签名（可选）

`win.signtoolOptions.sign` 指向 `scripts/sign.js`（electron-builder 26 的自定义
签名钩子位置）。未配置证书时**显式产出未签名包**并在构建日志中大声说明；
需要签名时在构建环境设置：

```powershell
set MOA_SIGN_PFX_FILE=C:\secrets\moagateway.pfx
set MOA_SIGN_PFX_PASSWORD=***
npm run dist
```

钩子会以该 PFX 对每个产物做 sha1+sha256 双重签名（RFC 3161 时间戳，
`http://timestamp.digicert.com`，可用 `signtoolOptions.rfc3161TimeStampServer`
覆盖），并对瞬时失败自动重试。

也可以不走该钩子的环境变量，直接用 electron-builder 官方机制：
`WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD` 环境变量，或在 yml 里配置
`win.signtoolOptions.certificateFile` / `certificatePassword`。

### 图标

`assets/icon.ico`（安装包/窗口）与 `assets/tray.png`（托盘）已随仓库提供。
如需重新生成：`npm run icons`（`scripts/generate-icons.js`，纯 Node 实现，
不依赖图像库；要求本机存在可用的源 SVG 时才会重写资产）。

---

## 数据与配置存放位置

| 内容 | 位置 |
| --- | --- |
| 桌面端配置 | `%APPDATA%/moa-gateway-desktop/desktop-config.json` |
| 加密后的 API Key | `%APPDATA%/moa-gateway-desktop/secrets.enc.json`（DPAPI 密文，绝不落盘明文） |
| 网关自身数据 | 网关仓库的 `data/`（日志、SQLite、JWT secret 等，由网关自己管理） |

配置项（均可在 UI 修改，带校验与默认值回退）：

```jsonc
{
  "gateway": {
    "pythonPath": "",            // 留空 = 自动探测
    "repoPath": "",              // 留空 = 自动探测
    "host": "127.0.0.1",         // 默认仅本机；0.0.0.0 需在 UI 显式选择
    "port": 8910,                // 首选端口，被占用自动顺延
    "autoStart": true,           // 应用启动时自动拉起网关
    "readyTimeoutMs": 120000     // 就绪超时
  },
  "health": { "pollIntervalMs": 5000, "startingPollIntervalMs": 1500, "failureThreshold": 3 },
  "restart": { "enabled": true, "baseMs": 1000, "factor": 2, "maxMs": 30000, "maxAttempts": 8 },
  "logs": { "maxLines": 5000, "maxBytes": 2097152 },
  "ui": { "minimizeToTray": true, "theme": "dark" },
  "system": { "openAtLogin": false }
}
```

---

## 安全设计

- 渲染进程 `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`；
  渲染层只能通过 `preload.js` 暴露的白名单 `window.moaGateway` API 与主进程通信
- API Key 明文只在保存瞬间经 IPC 进入主进程，主进程用 `safeStorage`（DPAPI）
  加密后才写盘；`list()` 永不返回明文，只返回名称与时间戳；加密不可用时拒绝写入
- 渲染壳经 `127.0.0.1` 回环 HTTP 提供（静态服务器仅绑定回环、拒绝路径穿越、
  不列目录、`no-store`），WebUI iframe 与之同协议，规避混合内容拦截
- 「浏览器打开」仅允许 `http(s)://127.0.0.1|localhost`，其余 URL 一律拒绝
- 网关默认绑定 `127.0.0.1`；暴露到局域网（0.0.0.0）是用户的显式选择
- 单实例锁防止重复的服务管理器

---

## 常见问题

**等待页一直转圈 / 报 "No usable Python interpreter found"**
安装网关依赖（`pip install -r requirements.txt`）或在「服务管理」里指定
`python.exe` 绝对路径，可用「自动探测环境」按钮验证实际解析结果。

**端口被占用**
无需处理：启动时自动探测并顺延端口，状态栏与「服务状态」显示实际监听端口。

**修改端口/主机/解释器后没生效**
这类变更需要重启服务（保存配置时 UI 会提示）。

**关闭主窗口后网关还在跑？**
这是设计使然：默认收进托盘以保持服务运行。托盘菜单 → Quit 才会真正停止
网关并退出。可在「系统」里关闭「收进托盘」。

**杀毒软件拦截未签名包**
本地构建默认未签名（构建日志会明确提示）。正式分发请配置签名证书后重建。
