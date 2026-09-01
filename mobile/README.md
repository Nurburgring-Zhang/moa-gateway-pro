# MOA Gateway — Capacitor Android 客户端

局域网 MOA Gateway（FastAPI 多 AI 网关）的移动端控制台。Capacitor 8 + 原生
Android 工程 + 零框架 TypeScript Web 层，直连网关 HTTP/SSE API，无中间服务、
无 mock、无占位逻辑。

## 功能

| 页面 | 能力 |
| --- | --- |
| 网关配置（首启） | 输入网关地址 + API Key；「测试连接」真实请求 `GET /health/ready`（无需鉴权）并附带 `GET /health` 版本/端点信息；填了 Key 时再用 `GET /v1/models` 校验 Key 有效性（401 ⇒ Key 无效） |
| 状态 | 健康灯（healthy/degraded/unhealthy 三态）+ 版本、端点健康数、真实/mock 端点统计、组件级就绪明细（含各组件延迟）；15 s 自动刷新（可关） |
| 对话 | 房间列表（`GET /v1/dialogue/rooms`）、新建房间（从 `GET /v1/models` 拉真实端点供勾选，≥2 个参与者、三种编排模式）、删除房间；聊天页对接 `POST /v1/dialogue/rooms/{id}/messages?stream=true` 与 `GET /v1/dialogue/rooms/{id}/stream` SSE：逐 token delta 流式渲染、按发言者分角色气泡、失败/超时/mock 显式标注、断流指数退避自动重连 |
| 设置 | 修改网关配置、应用锁开关、生物识别开关、修改/重置 PIN、清除配置 |
| 应用锁 | 启动门禁：优先原生 BiometricPrompt（`@aparajita/capacitor-biometric-auth`），任何失败（取消/锁定/无硬件/Web 环境）自动降级 6 位 PIN；PIN 仅存 `SHA-256(salt‖pin)`（Web Crypto），5 次错误锁 30 s，计数持久化 |

## 环境要求（构建机）

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Node.js | ≥ 20 | 含 npm |
| JDK | 21 | AGP 8.13 要求 Java 21（`capacitor.build.gradle` 亦固定 Java 21 编译选项） |
| Android SDK | compileSdk 36 / build-tools 随 AGP 8.13 自动下载 | `ANDROID_HOME` 指向 SDK 根目录，或安装 Android Studio |
| Gradle | 8.14.3（wrapper 自动下载发行版） | `android/gradle/wrapper/gradle-wrapper.jar` 已随仓库提交并经完整性校验；缺失时构建脚本会自动调用 `scripts/restore-gradle-wrapper.*` 从 Gradle 官方仓库恢复 |

网关侧要求：MOA Gateway 在本机/局域网可达（默认端口 `8910`，见
`moa_gateway/config.py` `ServerConfig.port`），且至少有一个 `enabled` 的真实
模型端点（创建对话房间时网关会校验 `endpoint_id` 必须存在于模型池）。

## 一条命令出 APK

```bash
# Linux / macOS（在 mobile/ 目录下）
./build-apk.sh              # release（默认）
./build-apk.sh debug        # debug
```

```powershell
# Windows PowerShell（在 mobile/ 目录下）
powershell -ExecutionPolicy Bypass -File .\build-apk.ps1            # release
powershell -ExecutionPolicy Bypass -File .\build-apk.ps1 -BuildType debug
```

脚本流水线：`npm ci` → `npm run build`（esbuild 打包 + `tsc --noEmit` 类型
检查）→ `npx cap sync android` → wrapper 完整性检查 → `gradlew assembleRelease`
→ 打印 APK 路径与大小。任何一步失败都会以非零码退出并给出原因。

### Release 签名（环境变量，绝不入库）

`android/app/build.gradle` 的 release 签名完全由环境变量驱动：

| 变量 | 含义 |
| --- | --- |
| `MOA_KEYSTORE_PATH` | keystore（.jks/.keystore）绝对路径 |
| `MOA_KEYSTORE_PASSWORD` | keystore 密码 |
| `MOA_KEY_ALIAS` | key 别名 |
| `MOA_KEY_PASSWORD` | key 密码 |

```bash
export MOA_KEYSTORE_PATH=/secrets/moa-release.keystore
export MOA_KEYSTORE_PASSWORD='...'
export MOA_KEY_ALIAS=moa-release
export MOA_KEY_PASSWORD='...'
./build-apk.sh
```

未设置 `MOA_KEYSTORE_PATH` 时 release 构建仍会成功，但**回退为 debug 签名**
（gradle 与构建脚本都会打印醒目警告）——该产物只能用于冒烟验证，禁止分发。

没有 keystore 时先生成一个：

```bash
keytool -genkeypair -v -keystore moa-release.keystore -alias moa-release \
  -keyalg RSA -keysize 2048 -validity 10000
```

## 手动构建（等价步骤）

```bash
npm ci
npm run build          # esbuild → www/js/app.js + tsc 类型检查
npx cap sync android   # 拷贝 www/ 与插件到 android/ 工程
cd android
./gradlew assembleRelease   # Windows: gradlew.bat
# 产物: android/app/build/outputs/apk/release/app-release.apk
```

安装到设备：`adb install android/app/build/outputs/apk/release/app-release.apk`
（debug 产物在 `apk/debug/app-debug.apk`）。

## 网关 CORS 配置（必须）

Capacitor Android 的 WebView 以 `https://localhost` 作为应用源
（见 `capacitor.config.ts` 的 `server.androidScheme: 'https'`）。网关的
CORSMiddleware 使用精确白名单（`moa_gateway/server.py`，`allow_credentials=True`），
因此必须在网关 `config.yaml` 中把应用源加入白名单，否则浏览器层会拦截跨源响应：

```yaml
server:
  cors_origins:
    - https://localhost        # ← MOA Gateway 移动端 App
    # ...保留你已有的其它 origin
```

改完重启网关生效。同时 `capacitor.config.ts` 已开启 `android.allowMixedContent`
并在 Manifest 声明 `usesCleartextTraffic`，因为局域网网关默认不带 TLS。

## 使用流程

1. 启动网关（`python start.py` 或 docker-compose），确认浏览器能打开
   `http://<网关IP>:8910/`。
2. 在网关 Web 控制台的 API Key 管理中生成一个 Key。
3. 安装 APK，首次启动输入 `http://<网关IP>:8910` 与 Key，点「测试连接」。
4. （建议）设置 → 开启应用锁：先设 6 位 PIN，再启用生物识别解锁。

## 工程结构

```
mobile/
├── package.json / package-lock.json    # Capacitor 8.5.0 + 生物识别插件 + esbuild/tsc
├── capacitor.config.ts                 # appId com.moagateway.console, webDir www
├── build-apk.ps1 / build-apk.sh        # 一键构建（npm ci → cap sync → gradle）
├── scripts/
│   ├── restore-gradle-wrapper.sh/.ps1  # wrapper jar 官方源恢复（仅缺失时触发）
│   └── generate-icons.js               # launcher 图标生成器（PNG 密度桶 + 矢量）
├── src/                                # TypeScript 源码（真实实现，无 mock）
│   ├── main.ts          # 启动时序：配置 → 锁门禁 → 三页 Tab 壳
│   ├── gateway.ts       # HTTP + SSE 客户端（fetch + ReadableStream 解析 SSE）
│   ├── security.ts      # 生物识别 + PIN（SHA-256 盐哈希、节流锁定）
│   ├── lock.ts          # 启动门禁：生物识别 → 任意失败降级 PIN
│   ├── state.ts/store.ts/types.ts/ui.ts
│   └── pages/           # setup / status / dialogue / room / settings
├── www/                                # Web 层（index.html + css + 打包产物）
└── android/                            # 完整原生工程
    ├── build.gradle / settings.gradle / variables.gradle / gradle.properties
    ├── gradle/wrapper/                 # wrapper 配置 + 已提交的官方 wrapper jar
    ├── gradlew / gradlew.bat
    └── app/
        ├── build.gradle                # debug/release 双 buildType + env 签名
        └── src/main/
            ├── AndroidManifest.xml     # INTERNET + USE_BIOMETRIC/USE_FINGERPRINT
            ├── java/.../MainActivity.java  # BridgeActivity
            └── res/                    # 主题/颜色/字符串/splash/矢量图标/mipmap
```

## 开发与调试

```bash
npm run watch        # esbuild 监听模式
npx cap sync android # 重新同步 www/ 到 android 工程
npx cap open android # Android Studio 打开原生工程（可跑模拟器/真机）
```

Web 层也可以在浏览器里直接预览（`www/` 用任意静态服务器托管）：存储自动降级
到 localStorage，生物识别显示为「Web 预览环境无原生生物识别，仅可使用 PIN」，
其余功能一致。

## 本机验证进度（如实声明）

交付方机器为 Windows 10 x64，**没有安装 Java / Android SDK，因此本机没有编译
APK——工程就绪、本机未编译 APK**。已在本机真实执行并通过的验证：

| 验证项 | 结果 |
| --- | --- |
| `npm ci`（干净安装全部依赖） | 通过：105 个包真实落盘——`@capacitor/core/cli/android@8.5.0`、`@capacitor/preferences@8.0.1`、`@aparajita/capacitor-biometric-auth@10.0.0`、esbuild、typescript |
| `npm run build`（esbuild 打包 + `tsc --noEmit`） | 通过：`www/js/app.js` 由 `src/*.ts` 真实打包产出，类型检查零错误 |
| `node --check www/js/app.js` | 通过（语法校验） |
| `www/index.html` 引用文件存在性 | 通过：`css/app.css`、`js/app.js` 均存在 |
| `npx cap sync android` | 通过：web 资产与插件注册同步进 `android/app/src/main/assets/` |
| gradle wrapper jar 完整性 | 通过：43,764 字节合法 zip，含 `org/gradle/wrapper/GradleWrapperMain.class`，与 properties 固定的 Gradle 8.14.3 对应 |
| 与网关契约核对 | 通过：鉴权（`Authorization: Bearer`）、dialogue 各端点请求/响应/SSE 事件字段逐一对照 `moa_gateway/routes/dialogue.py`、`moa_gateway/dialogue/models.py`、`moa_gateway/auth.py`、`moa_gateway/routes/health.py` |

**未在本机执行**：`gradlew assembleRelease`（需要 JDK 21 + Android SDK）。在
满足「环境要求」的机器上执行 `./build-apk.sh` 即可一条命令产出 APK；脚本对
缺 Java / 缺 SDK / 缺签名变量都会给出明确报错或警告，不会静默失败。
