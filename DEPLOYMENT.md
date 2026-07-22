# MoA Gateway Pro v1.7.3 — Deployment Guide

工业级多模型协作网关 - 部署文档

## 系统要求

- **OS**: Linux (Ubuntu 20.04+), macOS 12+, Windows Server 2019+
- **Python**: 3.11+
- **Memory**: 最少 1GB RAM,推荐 2GB+
- **Disk**: 最少 500MB
- **CPU**: 2 cores+ 推荐 (支持多 provider 并行)

## 快速开始 (Development)

```bash
# 1. 克隆仓库
git clone https://github.com/Nurburgring-Zhang/moa-gateway-pro.git
cd moa-gateway-pro

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 设置环境变量
export MOA_ADMIN_PASSWORD="YourStrong#Pass1"  # 必须 ≥6 位 + 字母 + 数字

# 5. 启动服务
python start.py
# 或: python -m uvicorn moa_gateway.server:app --host 0.0.0.0 --port 8088

# 6. 访问
# 浏览器: http://localhost:8088
# API: http://localhost:8088/docs (Swagger UI)
# Health: http://localhost:8088/health
```

## Docker 部署 (Production)

### 单机部署

```bash
# 1. 构建镜像
docker build -t moa-gateway-pro:1.7.3 .

# 2. 启动容器
docker run -d \
  --name moa-gateway \
  -p 8088:8088 \
  -e MOA_ADMIN_PASSWORD="YourStrong#Pass1" \
  -e OPENAI_API_KEY="sk-..." \
  -v moa_data:/app/data \
  -v moa_logs:/app/data/logs \
  --restart unless-stopped \
  moa-gateway-pro:1.7.3

# 3. 验证
curl http://localhost:8088/health
```

### Docker Compose 部署 (推荐)

```bash
# 1. 配置环境变量
export MOA_ADMIN_PASSWORD="YourStrong#Pass1"
export OPENAI_API_KEY="sk-..."
# (其他 provider keys 同理)

# 2. 启动
docker-compose up -d

# 3. 验证
curl http://localhost:8088/health

# 4. 查看日志
docker-compose logs -f moa-gateway

# 5. 停止
docker-compose down
```

## 集群部署 (Kubernetes)

```yaml
# k8s-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: moa-gateway
  namespace: ai
spec:
  replicas: 3
  selector:
    matchLabels:
      app: moa-gateway
  template:
    metadata:
      labels:
        app: moa-gateway
    spec:
      containers:
      - name: moa-gateway
        image: moa-gateway-pro:1.7.3
        ports:
        - containerPort: 8088
        env:
        - name: MOA_ADMIN_PASSWORD
          valueFrom:
            secretKeyRef:
              name: moa-secrets
              key: admin-password
        resources:
          requests:
            cpu: "1"
            memory: "1Gi"
          limits:
            cpu: "4"
            memory: "4Gi"
        livenessProbe:
          httpGet:
            path: /health
            port: 8088
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8088
          initialDelaySeconds: 5
          periodSeconds: 10
        volumeMounts:
        - name: data
          mountPath: /app/data
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: moa-data-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: moa-gateway
  namespace: ai
spec:
  type: LoadBalancer
  selector:
    app: moa-gateway
  ports:
  - port: 80
    targetPort: 8088
```

## 配置

### 配置文件 (config.yaml)

```yaml
server:
  host: 0.0.0.0
  port: 8088
  log_level: info
  cors_origins:
    - "http://localhost:3000"
    - "https://yourdomain.com"

auth:
  admin_username: admin
  admin_password: "YourStrong#Pass1"  # 必填

ratelimit:
  enabled: true
  per_key_rpm: 60
  per_key_daily_tokens: 5000000

storage:
  db_path: "./data/config.db"
  log_retention_days: 30
  log_dir: "./data/logs"

observability:
  log_dir: "./data/logs"
  log_json: true
```

### 环境变量覆盖

| 变量 | 描述 | 默认值 |
|------|------|--------|
| `MOA_ADMIN_PASSWORD` | 管理员密码 (≥6 位含字母+数字) | 必填 |
| `OPENAI_API_KEY` | OpenAI API key | 空 |
| `ANTHROPIC_API_KEY` | Anthropic API key | 空 |
| `DEEPSEEK_API_KEY` | DeepSeek API key | 空 |
| `ZHIPU_API_KEY` | 智谱 API key | 空 |
| `MOONSHOT_API_KEY` | Moonshot API key | 空 |
| `QWEN_API_KEY` | 通义千问 API key | 空 |
| `MOA_HOST` | 服务器 host | 0.0.0.0 |
| `MOA_PORT` | 服务器 port | 8088 |
| `MOA_LOG_LEVEL` | 日志级别 (debug/info/warning) | info |

## 监控

### 健康检查

```bash
# Liveness
curl -f http://localhost:8088/health

# Detailed health
curl http://localhost:8088/api/health/detailed

# Metrics (Prometheus)
curl http://localhost:8088/api/metrics
```

### 集成 Prometheus

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'moa-gateway'
    static_configs:
      - targets: ['moa-gateway:8088']
    metrics_path: '/api/metrics'
    scrape_interval: 15s
```

### 日志

```bash
# 应用日志
tail -f data/logs/gateway.log

# Docker 日志
docker logs -f moa-gateway

# 关键日志
grep "ERROR" data/logs/gateway.log
```

## 安全

### 1. 防火墙规则

```bash
# 仅开放 8088 端口
sudo ufw allow 8088/tcp
sudo ufw enable

# 限制管理 API 访问 (IP 白名单)
sudo ufw allow from 10.0.0.0/8 to any port 8088
```

### 2. HTTPS 反向代理 (Nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;
    
    ssl_certificate /etc/ssl/certs/yourdomain.crt;
    ssl_certificate_key /etc/ssl/private/yourdomain.key;
    
    location / {
        proxy_pass http://localhost:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 3. 密钥管理

- `.fernet_key` 和 `.jwt_secret` 存储在 data/ 目录,权限 0o600
- 管理员密码必须 ≥6 位含字母+数字
- API Key 通过 Bearer Token 鉴权

## 备份

```bash
# 备份数据目录 (包含 admin 凭据、API keys、配置)
tar -czf moa-backup-$(date +%Y%m%d).tar.gz data/

# 恢复
tar -xzf moa-backup-20260718.tar.gz
```

## 升级

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 备份数据
./scripts/backup.sh

# 3. 升级依赖
pip install -r requirements.txt --upgrade

# 4. 重启服务
docker-compose restart
# 或
systemctl restart moa-gateway
```

## 故障排查

### 启动失败

```bash
# 检查端口占用
netstat -tlnp | grep 8088

# 查看详细错误
python -m uvicorn moa_gateway.server:app --log-level debug
```

### 性能问题

- 减少 provider 数量
- 启用 connection pooling
- 调整 ratelimit 配置
- 监控 metrics: `/api/metrics`

### 常见错误

| 错误 | 原因 | 解决 |
|------|------|------|
| `admin_password 不能为空` | 缺少 MOA_ADMIN_PASSWORD | 设置环境变量 |
| `No module named 'moa_gateway'` | PYTHONPATH 未设置 | `export PYTHONPATH=.` |
| `Address already in use` | 端口被占用 | 关闭占用进程或改端口 |
| `database is locked` | SQLite 并发 | 重启服务,检查锁机制 |

## 容量规划

| 规模 | Memory | CPU | 网络 | 实例数 |
|------|--------|-----|------|--------|
| 个人开发者 (< 100 RPS) | 1GB | 1 core | 100Mbps | 1 |
| 小团队 (< 1000 RPS) | 2GB | 2 cores | 500Mbps | 2-3 |
| 企业级 (> 1000 RPS) | 4GB+ | 4 cores+ | 1Gbps+ | 5+ (集群) |

## 商业支持

- GitHub Issues: https://github.com/Nurburgring-Zhang/moa-gateway-pro/issues
- Email: support@moa-gateway-pro.com
- 商业版: 联系企业销售

## 附录

- [API 文档](http://localhost:8088/docs) - Swagger UI (运行时)
- [架构文档](ARCHITECTURE.md) - 系统架构
- [CHANGELOG](CHANGELOG.md) - 版本历史
- [CODE_REVIEW_SUBAGENT_A](CODE_REVIEW_SUBAGENT_A.md) - 代码审查报告
- [TEST_REVIEW_SUBAGENT_B](TEST_REVIEW_SUBAGENT_B.md) - 测试审查报告
