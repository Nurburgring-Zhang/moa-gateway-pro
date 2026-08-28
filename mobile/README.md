# MoA Gateway Pro — Android 客户端 (Capacitor)

真实实现的移动端客户端工程: 用 Capacitor 把「管理/编排」Web UI 包成 Android 原生应用,
并连接到**已部署的 MoA 网关**。

## 架构(诚实说明)
Python 不在 Android 设备上运行, 因此 Android 端是**客户端**, 连接一台部署好的 MoA 网关
(自托管服务器)。两种模式:
- **A 远程网关(推荐):** `capacitor.config.ts` 的 `server.url` 指向可达网关地址, Web UI 由
  网关提供并被原生壳包裹。
- **B 内置静态 UI:** 把 admin-ui 静态导出到 `dist/`, 设 `webDir:'dist'`, UI 通过网络调网关 API。

## 构建 APK
需要 **Android SDK + gradle** 的正常机器(审计沙箱无 Android SDK 且已禁容器, **不在沙箱内产出
`.apk` 二进制, 也绝不伪造**):
```bash
cd mobile
npm install
npx cap add android
npm run build:apk     # cd android && ./gradlew assembleDebug
```
产物: `android/app/build/outputs/apk/debug/app-debug.apk`。

## 连接编排引擎
App 内加载的 UI 含「智能编排」面板, 调用 `/v1/orchestrator/run` 等端点, 即与桌面端共用同一
套服务端编排引擎(O1-O8)。
