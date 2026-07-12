from pathlib import Path
fp = 'D:/MoA Gateway Pro/moa_gateway/ui/server_runner.py'
text = Path(fp).read_text(encoding='utf-8')

# 1. 在 __init__ 加 admin_token
old1 = '        # 启动回调列表:server 起来时自动调,让 page 重新加载数据\n        # (因为 page 启动时 sr.is_running=False → 早 return 不加载,需要这个钩子)\n        self.on_started_callbacks: list = []'
new1 = '        # 启动回调列表:server 起来时自动调,让 page 重新加载数据\n        # (因为 page 启动时 sr.is_running=False → 早 return 不加载,需要这个钩子)\n        self.on_started_callbacks: list = []\n        # 修25: admin JWT(自动 login 拿,所有 /api/* 端点都用这个)\n        self.admin_token: str = None'

assert old1 in text
text = text.replace(old1, new1)

# 2. 在 is_running = True 之后加 login_admin
old2 = '            if self._is_responsive():\n                self.is_running = True\n                # 触发所有注册的 on_started 回调\n                for cb in self.on_started_callbacks:\n                    try:\n                        cb()\n                    except Exception as e:\n                        logger.error("on_started callback failed: %s", e)\n                return True, f"运行在 :{port}"'
new2 = '            if self._is_responsive():\n                self.is_running = True\n                # 修25: 启动后自动 admin login 拿 token,UI 所有调用都用这个\n                self._admin_login()\n                # 触发所有注册的 on_started 回调\n                for cb in self.on_started_callbacks:\n                    try:\n                        cb()\n                    except Exception as e:\n                        logger.error("on_started callback failed: %s", e)\n                return True, f"运行在 :{port}"'

assert old2 in text
text = text.replace(old2, new2)

# 3. 加 _admin_login 方法
method = '''
    def _admin_login(self):
        """修25: server 启动后自动用默认 admin 登录,拿 JWT 存 self.admin_token。
        UI 所有 /api/* 调用都用这个 token(后端 auth.py 修24 也支持 admin JWT 鉴权)。"""
        import json as _json
        try:
            url = f"http://127.0.0.1:{self._port}/api/auth/login"
            payload = _json.dumps({"username": "admin", "password": "admin"}).encode()
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read().decode("utf-8"))
                self.admin_token = data.get("access_token")
                if self.admin_token:
                    logger.info("admin login OK, token len=%d", len(self.admin_token))
                else:
                    logger.warning("admin login returned no access_token: %s", data)
        except Exception as e:
            logger.warning("admin login failed (using default admin/admin): %s", e)
            self.admin_token = None
'''

# 加在 call_async 之前
old3 = '    def call_async(self, coro, timeout: float = 30):'
new3 = method + '    def call_async(self, coro, timeout: float = 30):'

assert old3 in text
text = text.replace(old3, new3)

Path(fp).write_text(text, encoding='utf-8')
print("OK - server_runner.py updated")
