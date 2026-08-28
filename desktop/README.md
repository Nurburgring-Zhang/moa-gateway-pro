# MoA Gateway Pro — Windows 桌面端 (Electron)

真实实现的桌面客户端入口: `main.js` 会用本机 Python 拉起 MoA 网关
(`uvicorn moa_gateway.server:app`), 轮询 `/health` 就绪后打开网关 Web 控制台
(含「智能编排」面板)。**逻辑为真实代码, 非占位。**

## 运行(开发)
```bash
cd desktop
npm install
npm start
```
需本机已安装 Python 与网关依赖(`pip install -r requirements.txt`)。

## 产出 Windows 安装包(.exe)
```bash
cd desktop
npm run dist        # 产 NSIS 安装包 (build/icon.ico 需提供图标)
```

> **诚实声明:** 最终 `.exe` 必须在 **Windows + Node** 环境用 electron-builder
> 产出; 审计沙箱为 Linux 且已禁容器, **不在此沙箱内产出 `.exe` 二进制**, 也绝不
> 伪造该二进制。本目录交付的是可在 Windows 机器上构建出 `.exe` 的完整、真实源码。

## 网关密钥
桌面端启动网关时透传 `MOA_ADMIN_PASSWORD` / `MOA_GATEWAY_KEY` / `MOA_JWT_SECRET`
环境变量; 未提供时网关按文档自动生成 admin 密码写入 `data/.admin_password`。
