"""moa_gateway.adapters — Agent 适配器配置生成
为各种 Agent(Hermes / OpenClaw / QoderWork / 通用 OpenAI 客户端)
生成对应的接入配置。

修21: 所有 adapter 现在都实现统一的 to_payload() 接口,返回:
    {
        "type": "...",
        "config": {...},     # 结构化配置(给机器看)
        "setup_md": "...",   # markdown 步骤(给人看)
        "examples": {...}    # cURL/Python 等可运行示例
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class AdapterContext:
    gateway_host: str
    gateway_port: int
    api_key: str
    https: bool = False

    @property
    def base_url(self) -> str:
        scheme = "https" if self.https else "http"
        return f"{scheme}://{self.gateway_host}:{self.gateway_port}"

    @property
    def openai_base(self) -> str:
        return f"{self.base_url}/v1"


class GenericOpenAIAdapter:
    """任何支持 OpenAI 协议的客户端"""

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx

    def get_curl_example(self) -> str:
        return f"""# 1) 列出模型
curl {self.ctx.openai_base}/models \\
  -H "Authorization: Bearer {self.ctx.api_key}"

# 2) 普通 chat
curl {self.ctx.openai_base}/chat/completions \\
  -H "Authorization: Bearer {self.ctx.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "model": "auto",
    "messages": [{{"role":"user","content":"Hello!"}}]
  }}'

# 3) MoA 协作
curl {self.ctx.openai_base}/chat/completions \\
  -H "Authorization: Bearer {self.ctx.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "model": "moa-balanced",
    "messages": [{{"role":"user","content":"请综合分析分布式系统设计中的 CAP 权衡"}}]
  }}'
"""

    def get_python_example(self) -> str:
        return f"""# pip install openai>=1.0
from openai import OpenAI

client = OpenAI(
    base_url="{self.ctx.openai_base}",
    api_key="{self.ctx.api_key}",
)

# 自动路由(简单/中等/复杂任务自动分配)
resp = client.chat.completions.create(
    model="auto",
    messages=[{{"role": "user", "content": "你好"}}],
    extra_body={{"preset": "balanced"}},
)
print(resp.choices[0].message.content)
"""

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "generic_openai",
            "config": {
                "api_base": self.ctx.openai_base,
                "api_key": self.ctx.api_key,
                "models": [
                    "auto",
                    "fast",
                    "balanced",
                    "quality",
                    "moa-balanced",
                    "moa-quality",
                    "pipeline",
                ],
            },
            "setup_md": (
                f"任何支持 OpenAI API 的客户端,改 base_url 为:\n"
                f"`{self.ctx.openai_base}`\n\n"
                f"模型别名: `auto` / `fast` / `balanced` / `quality` / "
                f"`moa-balanced` / `moa-quality` / `pipeline`"
            ),
            "examples": {
                "curl": self.get_curl_example(),
                "python": self.get_python_example(),
            },
        }

    # 兼容旧调用
    def to_dict(self) -> dict[str, Any]:
        return self.to_payload()


class HermesAdapter:
    """Hermes Agent 适配器"""

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx

    def get_config(self) -> dict[str, Any]:
        return {
            "model": {
                "provider": "custom",
                "model": "balanced",
                "custom": {
                    "base_url": self.ctx.openai_base,
                    "api_key": self.ctx.api_key,
                },
            },
            "moa": {
                "enabled": True,
                "default_preset": "balanced",
                "presets": {
                    "balanced": {
                        "reference_models": [
                            {"provider": "moa-gateway", "model": "auto"},
                            {"provider": "moa-gateway", "model": "balanced"},
                        ],
                        "aggregator": {"provider": "moa-gateway", "model": "quality"},
                    }
                },
            },
        }

    def to_payload(self) -> dict[str, Any]:
        cfg = self.get_config()
        return {
            "type": "hermes",
            "config": cfg,
            "setup_md": (
                f"# Hermes 接入 MoA Gateway Pro\n\n"
                f"```bash\n"
                f"hermes config set model.provider custom\n"
                f"hermes config set model.custom.base_url {self.ctx.openai_base}\n"
                f"hermes config set model.custom.api_key {self.ctx.api_key}\n"
                f"hermes config set moa.enabled true\n"
                f"```\n\n"
                f"或编辑 `~/.hermes/config.yaml` 加入上面的 config。\n\n"
                f"验证:\n```bash\n"
                f"hermes mcp test moa-gateway\n"
                f'hermes moa run "测试 MoA"\n```'
            ),
            "examples": {},
        }


class OpenClawAdapter:
    """OpenClaw 适配器"""

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx

    def get_config(self) -> dict[str, Any]:
        return {
            "models": {
                "mode": "merge",
                "providers": {
                    "moa-gateway": {
                        "type": "openai",
                        "base_url": self.ctx.openai_base,
                        "api_key": self.ctx.api_key or "dummy",
                        "timeout": 120,
                    }
                },
            },
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "moa-gateway/balanced",
                        "fallbacks": ["moa-gateway/fast", "moa-gateway/auto"],
                    }
                }
            },
        }

    def to_payload(self) -> dict[str, Any]:
        cfg = self.get_config()
        return {
            "type": "openclaw",
            "config": cfg,
            "setup_md": (
                f"# OpenClaw 接入 MoA Gateway Pro\n\n"
                f"环境变量方式:\n```bash\n"
                f"export OPENAI_BASE_URL={self.ctx.openai_base}\n"
                f"export OPENAI_API_KEY=<YOUR_API_KEY>\n"
                f"openclaw onboard\n```\n\n"
                f"配置文件 `~/.openclaw/openclaw.json`:\n```json\n"
                f"{json.dumps(cfg, ensure_ascii=False, indent=2)}\n```\n\n"
                f"验证: `openclaw doctor`"
            ),
            "examples": {},
        }


class QoderWorkAdapter:
    """QoderWork 适配器"""

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "qoderwork",
            "config": {
                "name": "MoA Gateway",
                "provider": "custom",
                "api_base": self.ctx.openai_base,
                "api_key": self.ctx.api_key,
                "model": "balanced",
            },
            "setup_md": (
                f"# QoderWork 接入 MoA Gateway Pro\n\n"
                f"1. 打开 Qoder 设置(右上角齿轮)\n"
                f"2. 左侧选择「模型」→「添加自定义模型」\n"
                f"3. 填写:\n"
                f"   - 名称: MoA Gateway\n"
                f"   - 提供商: Custom\n"
                f"   - API Base URL: {self.ctx.openai_base}\n"
                f"   - API Key: {self.ctx.api_key or 'your-api-key'}\n"
                f"   - 模型名: balanced\n"
                f"4. 点击「验证连接」\n"
                f"5. 在对话窗口选择「MoA Gateway」模型"
            ),
            "examples": {},
        }


class IDEAdapter:
    """通用 IDE 类适配器(Cursor / Cline / Continue.dev / Roo Code)"""

    def __init__(self, ctx: AdapterContext, name: str = "IDE"):
        self.ctx = ctx
        self.name = name

    def get_config(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "apiBase": self.ctx.openai_base,
            "apiKey": self.ctx.api_key,
            "model": "balanced",
        }

    def to_payload(self) -> dict[str, Any]:
        cfg = self.get_config()
        slug = self.name.lower().replace(" ", "_").replace(".", "")
        return {
            "type": f"ide_{slug}",
            "config": cfg,
            "setup_md": (
                f"# {self.name} 接入 MoA Gateway Pro\n\n"
                f"在 settings.json 中加入:\n```json\n"
                f"{json.dumps(cfg, ensure_ascii=False, indent=2)}\n```\n\n"
                f"或环境变量:\n```bash\n"
                f"export OPENAI_BASE_URL={self.ctx.openai_base}\n"
                f"export OPENAI_API_KEY=<YOUR_API_KEY>\n```"
            ),
            "examples": {},
        }


def all_adapters(ctx: AdapterContext) -> dict[str, Any]:
    """汇总所有适配器配置 — 修21: 统一返回 dict 格式"""
    return {
        "generic_openai": GenericOpenAIAdapter(ctx).to_payload(),
        "hermes": HermesAdapter(ctx).to_payload(),
        "openclaw": OpenClawAdapter(ctx).to_payload(),
        "qoderwork": QoderWorkAdapter(ctx).to_payload(),
        "ide": {
            "cursor": IDEAdapter(ctx, "Cursor").to_payload(),
            "cline": IDEAdapter(ctx, "Cline").to_payload(),
            "continue": IDEAdapter(ctx, "Continue.dev").to_payload(),
        },
    }
