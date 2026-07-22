# MoA Gateway Pro — UI 架构

## 桌面 UI(`flet` 模式 — 自带,默认)

**启动**:`python start_ui.py` / `start_ui.bat` / `start_ui.sh`

### 技术栈
- **flet 0.27**(Flutter for Python)— 自带 Skia 渲染,**不依赖系统 Qt/GTK/webview**
- **完全原生桌面窗口**,跨平台(Windows / macOS / Linux)
- 单进程:UI 进程内嵌后端 uvicorn server(独立 thread)

### 视觉
- **iOS 风格** · 圆角 14px · 卡片化 · 段控件 · 状态点
- **三主题**:`Auto`(跟随系统) / `Light` / `Dark`
- **状态栏**:时间 / 服务状态点 / 主题切换 / 启动按钮
- **侧栏**:logo + 6 个导航按钮(动画 150ms)
- **内容区**:6 个独立 page,header 28px 大标题

### 6 个 page
| Page | 功能 | 后端 |
|---|---|---|
| 📊 仪表盘 | 端点数 / 健康 / 密钥 / 花费 + 端点状态表 + 活动 + 快捷 | `/v1/models` `/api/*` |
| 🔌 模型端点 | DataTable + 增/改/删/启停 + 端点编辑 Dialog | `/api/endpoints/*` |
| 🧠 MoA 编排 | preset 选 + 5 个参数(参考数/温度/critic/层) + 输入/输出 + 真实调 `/v1/chat` | `/v1/chat` `/v1/moa/presets` |
| 📈 Benchmark | 4 tab:Suite / Pareto SVG / Layered / FLASK 雷达 SVG | `/v1/moa/{benchmark, cost-pareto, exec, flask}` |
| 📝 Prompt 模板 | ListView 选模板 + 大编辑器 + 保存/删除 | `/v1/moa/prompts/*` |
| ⚙️ 系统设置 | 主题切换 / 服务状态 / 改密 / 关于 | `/api/auth/change-password` |

### SVG 可视化
Benchmark 的 Pareto 散点图和 FLASK 雷达图都是**纯代码生成 SVG**,通过 `data:image/svg+xml;base64,...` 嵌入 `ft.Image`,无需外部库。

### 文件
```
moa_gateway/ui/
├── __init__.py      # 入口
├── main.py          # 主窗口 + 状态栏 + 侧栏 + 主题
├── theme.py         # 主题色板 (DARK / LIGHT)
├── server_runner.py # 内嵌 uvicorn server 线程管理
├── pages.py         # 仪表盘 + 端点 + 试玩台 + Settings
└── pages2.py        # Benchmark (4 tab) + Prompts
```

总代码 ~82 KB,行数约 1700 行。

---

## Web UI(`webui/index.html` — 备选)

旧的单文件 SPA 仍在 `moa_gateway/webui/index.html`,由 FastAPI 路由 `/webui/{name}` 提供。

保留原因:在远程服务器/无桌面环境下用浏览器访问。

---

## 主题色板

### Dark
- bg: `#0a0a0c`
- surface: `#16161a`
- card: `#1a1a1f`
- text: `#f5f5f7`
- accent: `#5e9cff`(iOS system blue)
- success: `#34c759`
- warning: `#ff9f0a`
- danger: `#ff453a`

### Light
- bg: `#f5f5f7`
- surface: `#ffffff`
- text: `#1c1c1e`
- accent: `#0a84ff`
