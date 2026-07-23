"""AI command suggester — natural language to CLI command mapping.

Fuses Warp's # prefix AI command suggestion with Terax's slash command
system. Provides keyword-based intent matching and optional LLM-enhanced
suggestions.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# --- Slash command registry (Terax-style) ---


SLASH_COMMANDS: dict[str, dict[str, Any]] = {
    "/help": {
        "description": "Show available commands",
        "usage": "/help",
        "handler": "help",
    },
    "/model": {
        "description": "Switch model",
        "usage": "/model <name>",
        "handler": "model",
    },
    "/moa": {
        "description": "Toggle MoA orchestration mode",
        "usage": "/moa",
        "handler": "moa",
    },
    "/clear": {
        "description": "Clear conversation history",
        "usage": "/clear",
        "handler": "clear",
    },
    "/history": {
        "description": "Show conversation history",
        "usage": "/history",
        "handler": "history",
    },
    "/quit": {
        "description": "Exit the REPL",
        "usage": "/quit",
        "handler": "quit",
    },
    "/workflow": {
        "description": "Execute or list workflows",
        "usage": "/workflow <name> | /workflow --list",
        "handler": "workflow",
    },
    "/discover": {
        "description": "Discover free model APIs",
        "usage": "/discover",
        "handler": "discover",
    },
    "/params": {
        "description": "Show parameter templates",
        "usage": "/params [task_type]",
        "handler": "params",
    },
    "/ai": {
        "description": "AI command suggestion mode",
        "usage": "/ai <natural language description>",
        "handler": "ai",
    },
    "/blocks": {
        "description": "Show interaction block history",
        "usage": "/blocks [last N]",
        "handler": "blocks",
    },
    "/export": {
        "description": "Export session history as JSON",
        "usage": "/export [filename]",
        "handler": "export",
    },
}


# --- Intent mapping rules (Warp-style # suggestion) ---


INTENT_MAP: list[dict[str, Any]] = [
    {
        "keywords": ["聊天", "对话", "chat", "talk", "说话", "问答"],
        "cmd": "chat",
        "args": {},
        "confidence": 0.9,
        "explanation": "Start an interactive chat session",
    },
    {
        "keywords": ["运行moa", "多模型", "混合", "moa", "orchestrate", "编排"],
        "cmd": "run-moa",
        "args": {},
        "confidence": 0.9,
        "explanation": "Run MoA (Mixture-of-Agents) orchestration",
    },
    {
        "keywords": ["查看模型", "模型列表", "有哪些模型", "models", "list models", "可用模型"],
        "cmd": "models",
        "args": ["list"],
        "confidence": 0.9,
        "explanation": "List all available models",
    },
    {
        "keywords": ["添加模型", "add model", "新增模型", "配置模型"],
        "cmd": "models",
        "args": ["add"],
        "confidence": 0.8,
        "explanation": "Add a new model endpoint",
    },
    {
        "keywords": ["发现免费", "搜索免费", "找免费模型", "discover", "free models", "免费模型", "免费"],
        "cmd": "discover",
        "args": ["--run"],
        "confidence": 0.95,
        "explanation": "Discover free model APIs",
    },
    {
        "keywords": ["提示词", "prompt模板", "prompts", "模板"],
        "cmd": "prompts",
        "args": ["--list"],
        "confidence": 0.85,
        "explanation": "List prompt templates",
    },
    {
        "keywords": ["工作流", "workflow", "编排流程", "流水线"],
        "cmd": "workflow",
        "args": ["--list"],
        "confidence": 0.9,
        "explanation": "List available workflows",
    },
    {
        "keywords": ["参数模板", "parameters", "params", "调参"],
        "cmd": "params",
        "args": ["--list"],
        "confidence": 0.85,
        "explanation": "List parameter templates",
    },
    {
        "keywords": ["配置", "config", "设置", "configuration"],
        "cmd": "config",
        "args": ["show"],
        "confidence": 0.85,
        "explanation": "Show current configuration",
    },
    {
        "keywords": ["mcp", "工具", "tools", "mcp工具"],
        "cmd": "mcp",
        "args": ["tools"],
        "confidence": 0.85,
        "explanation": "List MCP tools",
    },
    {
        "keywords": ["启动", "serve", "服务", "server", "运行网关"],
        "cmd": "serve",
        "args": {},
        "confidence": 0.9,
        "explanation": "Start the gateway server",
    },
    {
        "keywords": ["验证api", "verify", "test api", "测试接口", "api验证"],
        "cmd": "api-verify",
        "args": {},
        "confidence": 0.8,
        "explanation": "Verify API endpoint availability",
    },
]


class AICommandSuggester:
    """Natural language to CLI command suggester.

    Uses keyword-based intent matching for fast, offline suggestions.
    Optionally enhances with LLM when available.
    """

    def __init__(self) -> None:
        self.intent_map = INTENT_MAP

    async def suggest(self, natural_language: str) -> list[dict[str, Any]]:
        """Suggest CLI commands for a natural language input.

        Args:
            natural_language: User's natural language description.

        Returns:
            List of suggestion dicts, each containing:
            - cmd: CLI subcommand name
            - args: list of arguments
            - confidence: float 0-1
            - explanation: human-readable description
            - full_command: ready-to-execute command string
        """
        text = natural_language.lower().strip()
        if not text:
            return []

        suggestions: list[dict[str, Any]] = []

        for rule in self.intent_map:
            score = 0.0
            matched_keywords: list[str] = []
            for kw in rule["keywords"]:
                if kw.lower() in text:
                    # Longer keyword matches get higher score
                    score += len(kw) / 10.0
                    matched_keywords.append(kw)

            if score > 0:
                # Boost confidence based on number of matched keywords and total length
                boost = 1.0 + (len(matched_keywords) * 0.05) + (score / 10.0)
                confidence = min(rule["confidence"] * boost, 1.0)
                full_cmd = self._build_command(rule["cmd"], rule["args"])
                suggestions.append({
                    "cmd": rule["cmd"],
                    "args": rule["args"],
                    "confidence": round(confidence, 4),
                    "explanation": rule["explanation"],
                    "matched_keywords": matched_keywords,
                    "full_command": full_cmd,
                })

        # Sort by confidence descending
        suggestions.sort(key=lambda x: (x["confidence"], len(x.get("matched_keywords", []))), reverse=True)

        # Return top 5 suggestions
        return suggestions[:5]

    async def suggest_with_llm(
        self,
        natural_language: str,
        llm_call: Any,
    ) -> list[dict[str, Any]]:
        """Enhance suggestions using an LLM.

        Falls back to keyword-based suggestions if LLM fails.

        Args:
            natural_language: User's natural language description.
            llm_call: Async callable that takes messages and returns str.

        Returns:
            List of suggestion dicts (same format as suggest()).
        """
        # First get keyword-based suggestions
        base_suggestions = await self.suggest(natural_language)

        # Try LLM enhancement
        try:
            import json

            system_prompt = (
                "You are a CLI command suggester for 'moa' (MoA Gateway Pro). "
                "Available commands: serve, chat, run-moa, models, discover, "
                "prompts, mcp, config, params, workflow, api-verify.\n\n"
                "Given a natural language description, return a JSON array of "
                "suggestions. Each suggestion has: cmd, args (list), confidence "
                "(0-1), explanation. Return ONLY the JSON array."
            )
            user_prompt = f"Natural language: {natural_language}"

            response = await llm_call([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])

            # Parse LLM response
            cleaned = re.sub(r"```(?:json)?\s*", "", response).strip()
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if match:
                llm_suggestions = json.loads(match.group(0))
                # Merge LLM suggestions with keyword-based ones
                seen_cmds = {s["cmd"] for s in base_suggestions}
                for ls in llm_suggestions:
                    if isinstance(ls, dict) and ls.get("cmd") not in seen_cmds:
                        ls["full_command"] = self._build_command(
                            ls.get("cmd", ""),
                            ls.get("args", []),
                        )
                        base_suggestions.append(ls)

                base_suggestions.sort(
                    key=lambda x: x.get("confidence", 0), reverse=True
                )
                return base_suggestions[:5]

        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM suggestion failed, using keyword-based: %s", exc)

        return base_suggestions

    def _build_command(self, cmd: str, args: list[str] | dict[str, Any]) -> str:
        """Build a ready-to-execute command string."""
        parts = ["moa", cmd]
        if isinstance(args, list):
            parts.extend(str(a) for a in args)
        elif isinstance(args, dict):
            for k, v in args.items():
                if isinstance(v, bool):
                    if v:
                        parts.append(f"--{k}")
                elif v is not None:
                    parts.append(f"--{k}")
                    parts.append(str(v))
        return " ".join(parts)


# --- Slash command interception (Terax-style) ---


def try_intercept_slash(line: str) -> dict[str, Any]:
    """Check if a line is a slash command and return handler info.

    Inspired by Terax's tryRunSlashCommand function.

    Args:
        line: User input line.

    Returns:
        dict with keys:
        - handled: bool — whether this is a slash command
        - handler: str — handler name (if handled)
        - args: str — remaining arguments after the command
        - command: str — the slash command (e.g. "/model")
    """
    line = line.strip()
    if not line.startswith("/"):
        return {"handled": False}

    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()

    if cmd in SLASH_COMMANDS:
        args = parts[1] if len(parts) > 1 else ""
        return {
            "handled": True,
            "handler": SLASH_COMMANDS[cmd]["handler"],
            "args": args,
            "command": cmd,
            "description": SLASH_COMMANDS[cmd]["description"],
        }

    # Check for partial matches (e.g., /h matches /help)
    matches = [k for k in SLASH_COMMANDS if k.startswith(cmd)]
    if len(matches) == 1:
        args = parts[1] if len(parts) > 1 else ""
        return {
            "handled": True,
            "handler": SLASH_COMMANDS[matches[0]]["handler"],
            "args": args,
            "command": matches[0],
            "description": SLASH_COMMANDS[matches[0]]["description"],
        }
    elif len(matches) > 1:
        # Multiple matches: return the first one (sorted alphabetically)
        # and include alternatives in the result
        matches.sort()
        args = parts[1] if len(parts) > 1 else ""
        return {
            "handled": True,
            "handler": SLASH_COMMANDS[matches[0]]["handler"],
            "args": args,
            "command": matches[0],
            "description": SLASH_COMMANDS[matches[0]]["description"],
            "alternatives": matches[1:],
        }

    return {"handled": False, "unknown": cmd}


def is_ai_suggestion(line: str) -> bool:
    """Check if a line starts with # (AI suggestion mode)."""
    return line.strip().startswith("#")


def extract_suggestion_text(line: str) -> str:
    """Extract the natural language text from a # suggestion line."""
    return line.strip().lstrip("#").strip()


def build_cli_command(cmd: str, args: list[str] | None = None) -> str:
    """Build a CLI command string from command and arguments."""
    suggester = AICommandSuggester()
    return suggester._build_command(cmd, args or [])
