# 07 · 常见问题

## 7.1 启动问题

### Q: 启动报 "Address already in use" 怎么办?

A: 8910 端口被占用。两种方法:
- 方法 1:编辑 `config.yaml` 的 `server.port`,改一个空闲端口(如 8911)
- 方法 2:找出占用进程并结束
  - Windows: `netstat -ano | findstr :8910`,然后 `taskkill /PID <pid> /F`
  - Linux/macOS: `lsof -i :8910`,然后 `kill <pid>`

### Q: pip install 失败?

A: 通常是网络问题。尝试:
```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: 启动后立刻挂?

A: 看 `data/logs/gateway.log` 的报错。常见:
- `ModuleNotFoundError`:依赖没装全,重跑 pip install
- `sqlite3.OperationalError`:data 目录没写权限
- `Address already in use`:见上一条

## 7.2 模型接入

### Q: 接入 DeepSeek 后,调用一直 timeout?

A: 检查:
1. API Key 是否有效(到对应平台验证)
2. 网络是否可达(浏览器打开 base URL 看能不能通)
3. `timeout` 是否够(默认 120 秒,复杂任务可能需要更长)

### Q: 怎么接入私有部署的模型?

A: 选 Provider 为 `custom`,填自己的 base URL 即可。前提是它支持 OpenAI 兼容协议。

### Q: Anthropic Claude 怎么接?

A: Provider 选 `anthropic`,base URL 填 `https://api.anthropic.com`(注意**不要**带 `/v1`,我们会自动加)。

### Q: 一个模型能多个 Key 吗?

A: 当前不支持 — 一个 endpoint_id 对应一个 Key。如需多 Key,创建多个 endpoint_id(如 `gpt-4o-team-a` / `gpt-4o-team-b`)。

## 7.3 MoA 编排

### Q: MoA 一次调用多少 token?

A: 大致估算:
- 参考模型输入:`n × (用户 prompt + 一点点上下文)`(参考模型看不到 tool schema)
- 参考模型输出:`n × 用户预期输出长度`
- 聚合器输入:`所有参考输出 + 用户 prompt`(聚合器看到全部)
- 聚合器输出:用户预期输出长度
- 互审员输入/输出:小

举例(`balanced` preset, 4 参考 + 1 聚合 + 1 互审):
- 用户 prompt 500 tokens,期望输出 800 tokens
- 参考: 4 × (500 + 800) = 5200
- 聚合: 500 + 4×800 + 800 = 4500
- 互审: 800 + 200 = 1000
- 合计: ~10,700 tokens

### Q: MoA 太慢?

A: 几个优化:
- 用 `fast` preset(只 1 个 lite 模型)
- 减小 `reference_count`(如 2)
- 设 `critic_rounds: 0`
- 检查哪些参考模型慢,停用慢的

### Q: MoA 输出质量反而更差?

A: 排查:
1. 参考模型之间太相似(全用同一个 provider)→ 换不同 provider
2. 聚合器太弱(用了 lite)→ 升到 premium / flagship
3. 共识度太低(参考模型答非所问)→ 换更好的参考模型
4. critic 给的建议不靠谱 → 关掉 critic_rounds

### Q: 怎么看到 MoA 的完整过程?

A: WebUI 试玩台直接看,或 `GET /api/logs?limit=10` 取最近日志。
原生 MoA 端点 `POST /v1/moa/execute` 返回完整 JSON(参考、互审、共识度、成本、迭代数)。

## 7.4 性能 / 成本

### Q: 怎么估算月度成本?

A: 看 WebUI 仪表盘 — 总成本 / 总请求 × 你的预计月请求数 = 月度预估。

经验:
- 简单任务(`fast`):~$0.0005/次
- 中等任务(`balanced`):~$0.01/次
- 复杂任务(`quality`):~$0.04/次

### Q: 怎么压低成本?

A:
1. 默认用 `auto` 路由(简单任务不会被分配到旗舰)
2. 设 API Key 限额(防止异常消费)
3. 用 `fast` preset 做轻量任务
4. 国产模型优先(便宜)
5. 开日志保留清理(防止长期不维护)

### Q: 怎么调高吞吐?

A:
1. `server.workers: 4`(多 worker)— 但要确保 SQLite 兼容
2. API Key RPM 限额调高
3. 把 SQLite 换成 PostgreSQL(代码已抽象好,改 `Storage` 实现即可)
4. 部署到更强机器

## 7.5 安全

### Q: WebUI 怎么加固?

A:
1. 立即改默认 admin 密码
2. 反向代理加 basic auth 或 IP 白名单
3. CORS 不要配 `*`(生产环境改成具体域名)

### Q: 启动后所有 endpoint 都报 401,功能看起来无效?

A: 这是因为环境变量里设的 API key 是真的但被服务端拒绝(过期/余额用完/无效)。**v1.4.1+ 智能自动 fallback**:
- 启动时检测到 401/403/timeout 的 endpoint 自动切到 MockProvider(in-memory,不持久化)
- 3s startup timeout,启动不卡
- 所有 endpoint 立即可用,MoA 全部 preset 可跑
- 详情见 [09-auth-auto-fallback.md](09-auth-auto-fallback.md)

### Q: 填了正确 key 后,什么时候走真 API?

A: 重启 server 后自动重读 env var,key 正确就走真 API。Mock 状态只在内存中,重启就重置。

## 7.5 安全

### Q: WebUI 怎么加固?

A:
1. 立即改默认 admin 密码
2. 反向代理加 basic auth 或 IP 白名单
3. CORS 不要配 `*`(生产环境改成具体域名)

### Q: API Key 怎么存?

A: 加密存储(Fernet),密钥在 `data/.fernet_key`。**不要**把这个文件 commit 到 git。

### Q: 用户 prompt 会泄露吗?

A: 取决于你选的下游模型。本项目本身**不上传任何数据**到自己的服务器,数据全在本地 SQLite。请求转发到模型时,数据会到对应 provider — 这是模型 API 的固有限制,选模型时注意隐私政策。

## 7.6 故障

### Q: 某个模型一直 502?

A: 熔断器触发(连续失败 3 次)。查看:
1. WebUI 模型端点页 — 看 health / cooldown_remaining
2. 手动点「复位熔断」
3. 或 `POST /api/endpoints/{id}/reset-breaker`

### Q: 所有模型都失败?

A:
1. WebUI → 模型端点 → 确认至少 1 个 enabled + has_key
2. WebUI → 健康检查 → 看 healthy 状态
3. 检查网络(各 provider 域名)
4. 看 `data/logs/gateway.log` 具体错误

### Q: 调用时 503 Service Unavailable?

A: 没有任何可用模型,通常是:
- 所有端点都 `enabled=false`
- 所有端点都 `health=unhealthy`
- API Key 都没填

到 WebUI 检查即可。

## 7.7 部署

### Q: 怎么部署到服务器?

A:
1. 把整个项目目录 scp/rsync 到服务器
2. `python3 -m pip install -r requirements.txt`
3. `nohup python3 start.py serve > gateway.log 2>&1 &`
4. 配 systemd(可选) — 见下

**systemd 单元** (`/etc/systemd/system/moa-gateway.service`):
```ini
[Unit]
Description=MoA Gateway Pro
After=network.target

[Service]
Type=simple
User=moa
WorkingDirectory=/opt/moa-gateway-pro
ExecStart=/usr/bin/python3 start.py serve
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable moa-gateway
sudo systemctl start moa-gateway
sudo systemctl status moa-gateway
```

### Q: 怎么容器化?

A: 题目要求**禁止容器化**,但如果你必须:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8910
CMD ["python", "start.py", "serve"]
```

不过我们**不推荐**用 Docker — 这个项目本来就是为无容器化场景设计的。

### Q: 怎么备份数据?

A: 备份 `data/` 整个目录:
```bash
# Linux
tar czf moa-gateway-$(date +%Y%m%d).tar.gz data/

# Windows
# 用 7-Zip / WinRAR 压缩 data/ 目录
```

## 7.8 进阶

### Q: 怎么加新 Provider?

A: 在 `moa_gateway/providers/` 加新文件,实现 `Provider` 抽象类,然后在 `providers/__init__.py` 的 `_REGISTRY` 注册。

例:`providers/groq.py`
```python
from .base import Provider, ChatRequest, ChatResponse
from .openai_compat import OpenAICompatProvider

class GroqProvider(OpenAICompatProvider):
    async def health_check(self):
        # Groq 用 /openai/v1/models
        ...
```

### Q: 怎么贡献代码?

A: 欢迎 PR!关键原则:
- 保持向后兼容(API、配置 schema)
- 加单元测试(虽然 v1.0 还没加测试套件)
- 更新文档

### Q: 商业版和开源版的区别?

A: 当前是 MIT 开源版,无商业版。Roadmap 见 README。

## 7.9 联系 / 反馈

- GitHub Issues
- 邮件:(待补)
- 微信群:(待补)
