#!/usr/bin/env python3
"""MOA-Gateway-Pro 一键配置脚本

用法:
    python auto_setup.py          # 交互式配置
    python auto_setup.py --auto   # 自动模式（生成key，跳过可选项）
    python auto_setup.py --check  # 仅检查当前配置状态
"""
import os
import secrets
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
CONFIG_FILE = PROJECT_ROOT / "config.yaml"


def generate_api_key(prefix: str = "moa") -> str:
    """生成安全的API key"""
    return f"{prefix}-{secrets.token_urlsafe(32)}"


def check_status() -> bool:
    """检查当前配置状态"""
    print("\n" + "=" * 60)
    print("  MOA-Gateway-Pro 配置状态检查")
    print("=" * 60)

    # 检查.env文件
    if ENV_FILE.exists():
        print(f"\n\u2713 .env 文件存在: {ENV_FILE}")
    else:
        print(f"\n\u2717 .env 文件不存在（需要创建）")

    # 检查关键环境变量
    checks = {
        "MOA_GATEWAY_KEY": ("网关API Key", True),
        "MOA_ADMIN_PASSWORD": ("管理密码", True),
        "GROQ_API_KEY": ("Groq (免费LLM)", False),
        "GEMINI_API_KEY": ("Gemini (免费LLM)", False),
        "DEEPSEEK_API_KEY": ("DeepSeek (低价LLM)", False),
        "SILICONFLOW_API_KEY": ("SiliconFlow (国内免费)", False),
        "OPENAI_API_KEY": ("OpenAI (付费)", False),
        "ELEVENLABS_API_KEY": ("ElevenLabs (语音)", False),
        "TAVILY_API_KEY": ("Tavily (搜索)", False),
    }

    # 先加载.env文件
    env_vars: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

    # 合并当前环境变量
    for k in checks:
        if k not in env_vars:
            env_vars[k] = os.environ.get(k, "")

    has_gateway_key = False
    has_llm = False

    print("\n--- 核心配置 ---")
    for key, (desc, required) in checks.items():
        value = env_vars.get(key, "")
        if value:
            masked = value[:8] + "..." if len(value) > 8 else value
            print(f"  \u2713 {key}: {masked} ({desc})")
            if key == "MOA_GATEWAY_KEY":
                has_gateway_key = True
            if key in (
                "GROQ_API_KEY",
                "GEMINI_API_KEY",
                "DEEPSEEK_API_KEY",
                "SILICONFLOW_API_KEY",
                "OPENAI_API_KEY",
            ):
                has_llm = True
        else:
            marker = "\u2717 [必需]" if required else "\u25cb [可选]"
            print(f"  {marker} {key}: 未配置 ({desc})")

    # 总结
    print("\n--- 诊断结果 ---")
    if not has_gateway_key:
        print("  \u26a0 网关API Key未配置 \u2192 所有外部请求将被拒绝")
        print("    运行 `python auto_setup.py --auto` 自动生成")
    if not has_llm:
        print("  \u26a0 无任何LLM Key \u2192 所有模型调用将返回Mock数据")
        print("    推荐配置免费的 Groq 或 Gemini key")
    if has_gateway_key and has_llm:
        print("  \u2713 基本配置完整，可以真实运行！")

    print()
    return has_gateway_key and has_llm


def auto_setup():
    """自动模式：生成必需的key，创建.env"""
    print("\n" + "=" * 60)
    print("  MOA-Gateway-Pro 自动配置")
    print("=" * 60)

    # 如果.env不存在，从.env.example复制
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            shutil.copy2(ENV_EXAMPLE, ENV_FILE)
            print(f"\n\u2713 已从 .env.example 创建 .env")
        else:
            ENV_FILE.write_text("", encoding="utf-8")
            print(f"\n\u2713 已创建空 .env 文件")

    # 读取现有.env
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    env_dict: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, v = stripped.split("=", 1)
            env_dict[k.strip()] = v.strip()

    changes: list[str] = []

    # 生成 gateway key
    if not env_dict.get("MOA_GATEWAY_KEY"):
        key = generate_api_key("moa")
        env_dict["MOA_GATEWAY_KEY"] = key
        changes.append(f"  \u2713 生成网关API Key: {key}")

    # 生成 admin password
    if not env_dict.get("MOA_ADMIN_PASSWORD"):
        pwd = secrets.token_urlsafe(16)
        env_dict["MOA_ADMIN_PASSWORD"] = pwd
        changes.append(f"  \u2713 生成管理密码: {pwd}")

    # 写回.env
    if changes:
        # 重写.env文件
        new_lines: list[str] = []
        written_keys: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in env_dict:
                    new_lines.append(f"{k}={env_dict[k]}")
                    written_keys.add(k)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # 添加新key（不在原文件中的）
        for k, v in env_dict.items():
            if k not in written_keys and v:
                new_lines.append(f"{k}={v}")

        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        print("\n已自动配置:")
        for c in changes:
            print(c)
    else:
        print("\n\u2713 核心配置已存在，无需修改")

    # 打印使用指南
    gateway_key = env_dict.get("MOA_GATEWAY_KEY", "")
    print(f"""
{'=' * 60}
  配置完成！快速开始：
{'=' * 60}

1. 启动服务器:
   python -m uvicorn moa_gateway.server:app --port 8000

2. 测试API (用生成的key):
   curl http://localhost:8000/v1/chat/completions \\
     -H "Authorization: Bearer {gateway_key}" \\
     -H "Content-Type: application/json" \\
     -d '{{"model": "auto", "messages": [{{"role": "user", "content": "Hello"}}]}}'

3. 配置免费LLM (推荐):
   在 .env 中填入 GROQ_API_KEY 或 GEMINI_API_KEY
   获取地址: https://console.groq.com/keys

4. 查看配置状态:
   python auto_setup.py --check
""")


def interactive_setup():
    """交互式配置"""
    print("\n" + "=" * 60)
    print("  MOA-Gateway-Pro 配置向导")
    print("=" * 60)
    print("\n欢迎! 本向导将帮助你完成最小配置。\n")

    # Step 1: 网关key
    print("步骤 1/3: 网关API Key")
    print("-" * 40)
    gateway_key = input("  输入自定义key (直接回车自动生成): ").strip()
    if not gateway_key:
        gateway_key = generate_api_key("moa")
        print(f"  \u2192 已生成: {gateway_key}")

    # Step 2: Admin密码
    print("\n步骤 2/3: 管理后台密码")
    print("-" * 40)
    admin_pwd = input("  输入密码 (直接回车自动生成): ").strip()
    if not admin_pwd:
        admin_pwd = secrets.token_urlsafe(16)
        print(f"  \u2192 已生成: {admin_pwd}")

    # Step 3: LLM Key
    print("\n步骤 3/3: LLM API Key (至少1个)")
    print("-" * 40)
    print("  推荐免费选项:")
    print("    a) Groq     - https://console.groq.com/keys")
    print("    b) Gemini   - https://aistudio.google.com/apikey")
    print("    c) DeepSeek - https://platform.deepseek.com/api_keys")
    print("    d) 跳过 (将使用Mock响应)")

    llm_keys: dict[str, str] = {}
    choice = input("\n  选择 (a/b/c/d): ").strip().lower()
    if choice == "a":
        key = input("  Groq API Key: ").strip()
        if key:
            llm_keys["GROQ_API_KEY"] = key
    elif choice == "b":
        key = input("  Gemini API Key: ").strip()
        if key:
            llm_keys["GEMINI_API_KEY"] = key
    elif choice == "c":
        key = input("  DeepSeek API Key: ").strip()
        if key:
            llm_keys["DEEPSEEK_API_KEY"] = key

    # 写入.env
    env_content = f"""# MOA-Gateway-Pro 配置 (由setup向导生成)
MOA_GATEWAY_KEY={gateway_key}
MOA_ADMIN_PASSWORD={admin_pwd}
"""
    for k, v in llm_keys.items():
        env_content += f"{k}={v}\n"

    ENV_FILE.write_text(env_content, encoding="utf-8")
    print(f"\n\u2713 配置已写入 {ENV_FILE}")
    print(f"\n启动命令:")
    print(f"  python -m uvicorn moa_gateway.server:app --port 8000")


def main():
    args = sys.argv[1:]

    if "--check" in args:
        check_status()
    elif "--auto" in args:
        auto_setup()
    elif "--help" in args or "-h" in args:
        print(__doc__)
    else:
        # 默认交互式
        interactive_setup()


if __name__ == "__main__":
    main()
