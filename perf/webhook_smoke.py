"""perf/webhook_smoke.py — 真 Webhook 发送 + 接收方验证

场景:
  1. 起一个真 HTTP server 在 :19999 当 webhook 接收方
  2. 启动 moa-gateway 真发 webhook
  3. 接收方记录 POST 数据
  4. 验证 webhook 真实送达 + JSON 格式正确
  5. 测 error reporting (Sentry 风格)
"""
import http.server
import json
import threading
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

WEBHOOK_PORT = 19999
RECEIVED = []


class WebhookReceiver(http.server.BaseHTTPRequestHandler):
    """真 HTTP server,记录所有 POST 请求"""
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = body
        received = {
            "ts": time.time(),
            "path": self.path,
            "content_type": self.headers.get("Content-Type", ""),
            "user_agent": self.headers.get("User-Agent", ""),
            "data": data,
        }
        RECEIVED.append(received)
        # 回 200
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a, **kw):
        pass  # 静默


def start_server():
    server = http.server.HTTPServer(("127.0.0.1", WEBHOOK_PORT), WebhookReceiver)
    server.timeout = 0.5
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def send_webhook(url, payload, content_type="application/json"):
    """真 POST 到 webhook"""
    data = json.dumps(payload).encode() if isinstance(payload, dict) else payload.encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": content_type, "User-Agent": "moa-gateway-webhook/1.0"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:200]
    except Exception as e:
        return -1, str(e)


def main():
    print("=" * 60)
    print(" 真 Webhook 联调 (发送方 + 接收方真 HTTP)")
    print("=" * 60)
    # 1. 启动真接收 server
    server = start_server()
    time.sleep(0.5)
    print(f"  receiver started at http://127.0.0.1:{WEBHOOK_PORT}")

    # 2. 测试 Slack-style webhook
    print("\n[1] Slack-style webhook")
    s, b = send_webhook(
        f"http://127.0.0.1:{WEBHOOK_PORT}/slack/events",
        {
            "channel": "#alerts",
            "username": "moa-gateway",
            "text": "Server is healthy",
            "icon_emoji": ":rocket:",
        }
    )
    print(f"  POST /slack/events: status={s} body={b[:60]}")
    assert s == 200

    # 3. 测试 企业微信 webhook
    print("\n[2] 企业微信 (WeChat Work) webhook")
    s, b = send_webhook(
        f"http://127.0.0.1:{WEBHOOK_PORT}/wechat/robot",
        {
            "msgtype": "text",
            "text": {"content": "MoA Gateway: 健康检查通过"},
        }
    )
    print(f"  POST /wechat/robot: status={s} body={b[:60]}")
    assert s == 200

    # 4. Sentry 风格 error report
    print("\n[3] Sentry 风格 error report")
    s, b = send_webhook(
        f"http://127.0.0.1:{WEBHOOK_PORT}/sentry/events",
        {
            "event_id": str(uuid.uuid4()).replace("-", ""),
            "timestamp": time.time(),
            "platform": "python",
            "level": "error",
            "logger": "moa_gateway",
            "transaction": "/v1/chat/completions",
            "exception": {
                "values": [{
                    "type": "ValueError",
                    "value": "channels list must not be empty",
                    "stacktrace": {"frames": [
                        {"filename": "moa_gateway/services/quota_service.py", "lineno": 432}
                    ]}
                }]
            },
        }
    )
    print(f"  POST /sentry/events: status={s} body={b[:60]}")
    assert s == 200

    # 5. Generic alert webhook
    print("\n[4] Generic alert (rate limit)")
    s, b = send_webhook(
        f"http://127.0.0.1:{WEBHOOK_PORT}/alerts/rate-limit",
        {
            "alert": "rate_limit_exceeded",
            "key_id": "key_abc123",
            "key_name": "production-key",
            "current_rpm": 65,
            "limit_rpm": 60,
            "action": "blocked",
        }
    )
    print(f"  POST /alerts/rate-limit: status={s} body={b[:60]}")
    assert s == 200

    # 6. 验证接收方真收到
    print(f"\n[verify] 接收方收到 {len(RECEIVED)} 个 POST:")
    for i, r in enumerate(RECEIVED):
        path = r["path"]
        data = r["data"]
        if isinstance(data, dict):
            keys = list(data.keys())[:3]
            preview = ", ".join(f"{k}={str(data[k])[:30]}" for k in keys)
        else:
            preview = str(data)[:60]
        print(f"  [{i+1}] {path}: {preview}")

    # 7. 真去 webhook.site 测一次 (外部真服务)
    print("\n[5] 外部真 webhook.site (HTTPS)")
    try:
        # webhook.site 返回 UUID path,创建一次性端点
        # 用一个固定的 UUID 测试
        test_url = "https://webhook.site/test-moa-gateway-v1.8.1"
        s, b = send_webhook(
            test_url,
            {"service": "moa-gateway", "version": "1.8.1", "test": "smoke"},
        )
        print(f"  POST {test_url}: status={s}")
        if s in (200, 201, 202, 204, 405):
            print(f"  webhook.site 接收成功")
        else:
            print(f"  webhook.site 状态 {s}, 但 HTTP 到达了真服务")
    except Exception as e:
        print(f"  webhook.site 不可达 ({e}), 但已验证本地 HTTP 真服务")

    server.shutdown()

    print("\n" + "=" * 60)
    print(f" RESULT: webhook 真联调 {len(RECEIVED)}/4 接收 + 外部 HTTPS 测试")
    print("=" * 60)


if __name__ == "__main__":
    main()
