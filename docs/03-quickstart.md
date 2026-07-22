# 03 · 5 分钟快速开始

## 3.1 准备

- **Python 3.10+** (建议 3.11)
- 至少一个模型的 API Key(国产 / 国外均可)
- 操作系统:Windows / Linux / macOS

## 3.2 自愈启动(推荐)

**最简单的方式 — 跑一行命令就行:**

**Windows**:
```cmd
cd D:\MoA Gateway Pro
start.bat
```

**Linux / macOS**:
```bash
cd /path/to/MoA\ Gateway\ Pro
./start.sh
```

或:
```bash
python start.py serve
```

### 启动时会发生什么(自愈流程)

启动时,程序会**自动**做这 5 步体检,任何一步失败都会自动修复:

```
[1/5] 虚拟环境
  → 检查 venv/ 是否存在
  → 不存在?python -m venv 自动创建
  → 当前进程不在 venv 里?os.execv 切到 venv 重新拉起

[2/5] 关键 Python 包(17 个)
  → 逐个 import 测试
  → 缺失或损坏?按以下顺序重试:
     1. 清华源 (pypi.tuna.tsinghua.edu.cn)
     2. 阿里云 (mirrors.aliyun.com)
     3. 豆瓣 (pypi.douban.com)
     4. 华为云 (mirrors.huaweicloud.com)
     5. PyPI 官方 (回退)
  → 每个源 2 次重试,带 600s 超时
  → 装完重试,不行就明确报错

[3/5] 数据目录与关键文件
  → 检查 data/ 是否存在、可写
  → 检查 JWT secret (.jwt_secret)
  → 检查 Fernet key (.fernet_key)
  → 缺失?用 cryptography 自动生成

[4/5] FastAPI app 能否加载
  → 尝试 import 整个 server 模块
  → 检查 29 个路由都注册成功
  → 失败就明确报错(哪个 import 坏了)

[5/5] 端口检查
  → 默认 8910 端口是否空闲
  → 占用?提示改 config.yaml 或杀进程

都通过 → 起 watchdog 父进程
watchdog 拉起 uvicorn 子进程
子进程死了 → 自动重启
关窗/退出 → 全部子进程清理
```

**这个流程的意义**:
- 用户**不需要**手动 pip install
- 用户**不需要**手动建 venv
- 用户**不需要**懂 Python 依赖管理
- 第一次跑、第十次跑、第 N 次跑,行为完全一致

### 启动后日志长这样(写到 `data/heal.log`)

```
============================================================
MoA Gateway Pro 启动自愈流程
============================================================

[1/5] 检查虚拟环境…
  → 缺失,创建中…
  [fix] 创建 venv: D:\MoA Gateway Pro\venv
  → 切到 venv 重启: D:\MoA Gateway Pro\venv\Scripts\python.exe
  ✓ venv OK

[2/5] 检查关键 Python 包…
  → 缺失: httpx, aiohttp
  [pip] 试源 清华 (...) 第 1/2 次: ...
  [ok] 装包成功(源 清华)
  ✓ 全部 17 个关键包 OK

[3/5] 检查数据目录与关键文件…
  [fix] 生成 JWT secret → D:\MoA Gateway Pro\data\.jwt_secret
  [fix] 生成 Fernet key → D:\MoA Gateway Pro\data\.fernet_key
  ✓ data 目录与关键文件 OK

[4/5] 检查 FastAPI app 能加载…
  ✓ app 正常, 29 个路由

[5/5] 检查端口…
  ✓ 127.0.0.1:8910 空闲

============================================================
环境自愈完成,耗时 12.3s
============================================================

启动 watchdog + uvicorn…
  WebUI:  http://127.0.0.1:8910/
  API:    http://127.0.0.1:8910/v1/
```

## 3.3 直接启动(开发用)

如果不想走自愈,直接当前 Python 跑:

```bash
pip install -r requirements.txt
python start.py direct            # 直接跑(无 venv,无 watchdog)
python start.py direct --reload   # 加热重载
```

## 3.4 接入第一个模型

### 3.3.1 打开 WebUI

浏览器访问 [http://localhost:8910/](http://localhost:8910/),
默认账号:
- 用户名:`admin`
- 密码:`admin`

⚠️ **生产部署前请立即修改默认密码**。

### 3.3.2 添加模型端点

进入「**模型端点**」→「**+ 新增端点**」,填入:

**示例 1:DeepSeek(国产便宜)**
- ID: `deepseek-v3`
- Provider: `deepseek`
- Model: `deepseek-chat`
- Tier: `standard`
- API Base: `https://api.deepseek.com/v1`
- API Key: 你的 DeepSeek API Key
- 其它默认

**示例 2:OpenAI GPT-4o-mini(国外便宜)**
- ID: `gpt-4o-mini`
- Provider: `openai`
- Model: `gpt-4o-mini`
- Tier: `lite`
- API Base: `https://api.openai.com/v1`
- API Key: 你的 OpenAI API Key

**示例 3:Anthropic Claude 3.5 Sonnet**
- ID: `claude-sonnet`
- Provider: `anthropic`
- Model: `claude-3-5-sonnet-latest`
- Tier: `premium`
- API Base: `https://api.anthropic.com`
- API Key: 你的 Anthropic API Key

> **小技巧**:可以先只接入 1-2 个模型(便宜的国产 + 贵的旗舰)就够了。
> 后面随时增删,改完实时生效,不用重启。

### 3.3.3 生成 API Key

进入「**API 密钥**」→「**+ 新建 Key**」:
- 名称:随便,如 `hermes-prod`
- RPM:60
- 每日 token:5,000,000

> ⚠️ 创建后 Key 文本**只显示一次**,请立即复制保存!

## 3.4 第一次调用

### 3.4.1 用 WebUI 试玩

进入「**试玩台**」:
- 模型选 `auto` (智能路由)
- 输入:`你好,简单介绍下你自己`
- 点「🚀 发送」

### 3.4.2 用 curl 测试

```bash
curl http://localhost:8910/v1/chat/completions \
  -H "Authorization: Bearer mgw-你生成的key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 3.4.3 用 OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8910/v1",
    api_key="mgw-你生成的key"
)

r = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "你好"}]
)
print(r.choices[0].message.content)
```

## 3.5 体验 MoA 多模型协作

**前提**:至少接入 **3 个不同 Provider 的模型**(建议 2 个国产 + 1 个旗舰)。

在试玩台:
- 模型选 `balanced` (4 个模型并行 + 旗舰聚合)
- 输入:`请分析分布式系统设计中的 CAP 权衡,给出具体场景下的取舍建议`
- 点「🚀 发送」

你会看到:
- **最终答案**(聚合器综合的输出)
- **参考模型详情**(4 个模型的独立提案,可对比)
- **互审员反馈**(critic 给出的问题与建议)
- **共识度、成本、延迟、迭代轮数**

## 3.6 在 Agent 里使用

详见 [05-agent-integration.md](05-agent-integration.md)。

最简单的方式 — 在 WebUI「**接入 Agent**」页面,直接复制对应 Agent 的配置片段。

## 3.7 下一步

- [04-api-reference.md](04-api-reference.md) — 完整 API 文档
- [06-moa-deep-dive.md](06-moa-deep-dive.md) — MoA 编排深度解析
- [07-faq.md](07-faq.md) — 常见问题
