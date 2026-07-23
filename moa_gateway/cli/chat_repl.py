"""Interactive chat REPL for MoA Gateway Pro.

Enhanced with Warp/Terax-style Block I/O (P1-5):
- ChatBlock: typed interaction unit (user input, AI response, tool call, etc.)
- BlockManager: manages block sequence, rendering, and export
- /blocks command: view interaction history
- /export command: export session as JSON
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


# ============ Block I/O System (P1-5) ============


class BlockType(Enum):
    """Type of interaction block (Warp-inspired)."""

    USER_INPUT = "user_input"
    AI_RESPONSE = "ai_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    SYSTEM = "system"
    WORKFLOW = "workflow"


class BlockStatus(Enum):
    """Execution status of a block."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass
class ChatBlock:
    """A single interaction block (Warp Block I/O concept).

    Each interaction unit (user input, AI reply, tool call, error) is
    represented as a Block that can be rendered, searched, and exported.
    """

    id: str
    type: BlockType
    title: str
    content: str = ""
    status: BlockStatus = BlockStatus.SUCCESS
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list["ChatBlock"] = field(default_factory=list)

    def render(self, rich: bool = False) -> str:
        """Render block as displayable text.

        Args:
            rich: If True, use ANSI color codes for terminal display.
        """
        status_icon = {
            BlockStatus.PENDING: "...",
            BlockStatus.RUNNING: ">>>",
            BlockStatus.SUCCESS: "[OK]",
            BlockStatus.FAILURE: "[FAIL]",
        }.get(self.status, "")

        type_label = self.type.value.upper()
        time_str = self.timestamp.strftime("%H:%M:%S")

        if rich:
            # ANSI colors
            type_colors = {
                BlockType.USER_INPUT: "\033[36m",  # cyan
                BlockType.AI_RESPONSE: "\033[32m",  # green
                BlockType.TOOL_CALL: "\033[33m",   # yellow
                BlockType.TOOL_RESULT: "\033[33m",  # yellow
                BlockType.ERROR: "\033[31m",        # red
                BlockType.SYSTEM: "\033[90m",       # gray
                BlockType.WORKFLOW: "\033[35m",     # magenta
            }
            color = type_colors.get(self.type, "")
            reset = "\033[0m"
            header = f"{color}[{time_str}] {type_label} {status_icon}{reset} {self.title}"
        else:
            header = f"[{time_str}] {type_label} {status_icon} {self.title}"

        lines = [header]
        if self.content:
            for line in self.content.split("\n"):
                lines.append(f"  {line}")

        for child in self.children:
            for line in child.render(rich=rich).split("\n"):
                lines.append(f"  {line}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize block to dictionary for JSON export."""
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "content": self.content,
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "children": [c.to_dict() for c in self.children],
        }


class BlockManager:
    """Manages the sequence of interaction blocks.

    Provides add, update, query, render, and export capabilities.
    Implements LRU eviction to prevent memory growth in long sessions.
    """

    MAX_BLOCKS = 500

    def __init__(self, max_blocks: int = MAX_BLOCKS) -> None:
        self._blocks: list[ChatBlock] = []
        self._max_blocks = max_blocks

    def add_block(self, block: ChatBlock) -> str:
        """Add a block and return its ID.

        Evicts oldest blocks if max capacity is reached.
        """
        self._blocks.append(block)
        if len(self._blocks) > self._max_blocks:
            self._blocks = self._blocks[-self._max_blocks:]
        return block.id

    def update_block(self, block_id: str, **kwargs: Any) -> bool:
        """Update a block's attributes. Returns True if found."""
        for block in self._blocks:
            if block.id == block_id:
                for key, value in kwargs.items():
                    if hasattr(block, key):
                        setattr(block, key, value)
                return True
        return False

    def get_blocks(
        self, block_type: BlockType | None = None
    ) -> list[ChatBlock]:
        """Get blocks, optionally filtered by type."""
        if block_type is None:
            return list(self._blocks)
        return [b for b in self._blocks if b.type == block_type]

    def search_blocks(self, query: str) -> list[ChatBlock]:
        """Search blocks by substring in title or content."""
        query_lower = query.lower()
        return [
            b for b in self._blocks
            if query_lower in b.title.lower()
            or query_lower in b.content.lower()
        ]

    def render_history(self, last_n: int = 10) -> str:
        """Render the last N blocks as formatted text."""
        blocks = self._blocks[-last_n:] if last_n > 0 else self._blocks
        if not blocks:
            return "(no blocks)"
        return "\n\n".join(b.render() for b in blocks)

    def render_history_rich(self, last_n: int = 10) -> str:
        """Render the last N blocks with ANSI colors."""
        blocks = self._blocks[-last_n:] if last_n > 0 else self._blocks
        if not blocks:
            return "(no blocks)"
        return "\n\n".join(b.render(rich=True) for b in blocks)

    def export_history(self) -> list[dict[str, Any]]:
        """Export all blocks as a list of dictionaries (JSON-serializable)."""
        return [b.to_dict() for b in self._blocks]

    def clear(self) -> None:
        """Clear all blocks."""
        self._blocks.clear()

    @property
    def count(self) -> int:
        """Total number of blocks."""
        return len(self._blocks)


# ============ Chat REPL ============


class ChatREPL:
    """Interactive REPL for chatting with models or running MoA orchestration.

    Slash Commands (Terax-style):
        /model <name>   Switch model
        /moa            Toggle MoA mode
        /clear          Clear conversation history
        /history        Show conversation history
        /workflow       Execute or list workflows
        /discover       Discover free models
        /params         Show parameter templates
        /ai <text>      AI command suggestion
        /blocks [N]     Show last N interaction blocks
        /export [file]  Export session history as JSON
        /help           Show help
        /quit           Exit

    AI Suggestion (Warp-style):
        # <natural language>   Get CLI command suggestions
    """

    def __init__(
        self,
        model: str = "auto",
        base_url: str = "http://127.0.0.1:8910",
        api_key: str = "",
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.history: list[dict[str, str]] = []
        self.moa_mode = False
        self._blocks = BlockManager()

    def run(self):
        print(f"MoA Gateway Pro Chat (model={self.model})")
        print("Commands: /model <name>, /moa, /clear, /history, /workflow, /discover, /ai, /blocks, /export, /quit")
        print("AI Suggestion: # <natural language description>")
        print()

        while True:
            try:
                line = input(">>> ").strip()
                if not line:
                    continue

                # Check for # prefix (Warp-style AI suggestion)
                if line.startswith("#"):
                    self._blocks.add_block(ChatBlock(
                        id=str(uuid4()),
                        type=BlockType.USER_INPUT,
                        title="AI Suggestion",
                        content=line,
                    ))
                    self._handle_ai_suggestion(line[1:].strip())
                    continue

                # Check for / prefix (Terax-style slash commands)
                if line.startswith("/"):
                    self._blocks.add_block(ChatBlock(
                        id=str(uuid4()),
                        type=BlockType.USER_INPUT,
                        title="Command",
                        content=line,
                    ))
                    self._handle_command(line)
                    continue

                self._send(line)

            except (KeyboardInterrupt, EOFError):
                print("\nBye!")
                break

    def _handle_command(self, cmd: str):
        from moa_gateway.cli.ai_suggest import try_intercept_slash

        result = try_intercept_slash(cmd)
        if not result.get("handled"):
            error_block = ChatBlock(
                id=str(uuid4()),
                type=BlockType.ERROR,
                title="Unknown Command",
                content=cmd,
                status=BlockStatus.FAILURE,
            )
            self._blocks.add_block(error_block)
            print(f"Unknown command: {cmd.split()[0]}  (try /help)")
            return

        handler = result["handler"]
        args = result.get("args", "")

        if handler == "quit":
            raise EOFError
        elif handler == "model":
            if args:
                self.model = args
                print(f"Switched to model: {self.model}")
            else:
                print(f"Current model: {self.model}")
        elif handler == "moa":
            self.moa_mode = not self.moa_mode
            print(f"MOA mode: {'ON' if self.moa_mode else 'OFF'}")
        elif handler == "clear":
            self.history = []
            self._blocks.clear()
            print("History cleared")
        elif handler == "history":
            if not self.history:
                print("(empty)")
            for msg in self.history:
                role = msg["role"]
                content = msg["content"][:80]
                print(f"  [{role}] {content}{'...' if len(msg['content']) > 80 else ''}")
        elif handler == "help":
            self._show_help()
        elif handler == "workflow":
            self._handle_workflow(args)
        elif handler == "discover":
            self._handle_discover()
        elif handler == "params":
            self._handle_params(args)
        elif handler == "ai":
            self._handle_ai_suggestion(args)
        elif handler == "blocks":
            self._handle_blocks(args)
        elif handler == "export":
            self._handle_export(args)
        else:
            print(f"Command handler '{handler}' not implemented")

    def _handle_blocks(self, args: str):
        """Handle /blocks command -- show interaction block history."""
        try:
            n = int(args) if args else 10
        except ValueError:
            n = 10
        print(self._blocks.render_history(last_n=n))
        print(f"\nTotal blocks: {self._blocks.count}")

    def _handle_export(self, args: str):
        """Handle /export command -- export session history as JSON."""
        export_data = {
            "model": self.model,
            "moa_mode": self.moa_mode,
            "exported_at": datetime.now().isoformat(),
            "conversation": self.history,
            "blocks": self._blocks.export_history(),
        }
        json_str = json.dumps(export_data, indent=2, ensure_ascii=False)

        if args:
            filepath = args
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print(f"Session exported to {filepath} ({len(json_str)} bytes)")
            except Exception as e:
                print(f"Export failed: {e}")
        else:
            print(json_str)

    def _handle_ai_suggestion(self, text: str):
        """Handle # prefix AI command suggestion."""
        if not text:
            print("Usage: # <natural language description>")
            print("Example: # list all available models")
            return

        from moa_gateway.cli.ai_suggest import AICommandSuggester

        suggester = AICommandSuggester()
        suggestions = asyncio.run(suggester.suggest(text))

        if not suggestions:
            print(f"No matching commands found for: '{text}'")
            return

        print(f"\nSuggested commands for: '{text}'\n")
        for i, s in enumerate(suggestions, 1):
            print(f"  {i}. {s['full_command']}")
            print(f"     Confidence: {s['confidence']:.0%} -- {s['explanation']}")

        print(f"\nTo execute, run the command in your terminal.")
        print()

    def _handle_workflow(self, args: str):
        """Handle /workflow command."""
        if not args or args == "--list":
            try:
                from moa_gateway.workflows.workflow_loader import WorkflowLoader

                loader = WorkflowLoader()
                workflows = loader.list_workflows()
                if not workflows:
                    print("No workflows found")
                    return
                print(f"\nAvailable workflows ({len(workflows)}):")
                for w in workflows:
                    print(f"  {w['name']:<30s}  {w['description'][:50]}")
            except Exception as e:
                print(f"Error: {e}")
        else:
            try:
                from moa_gateway.workflows.workflow_loader import WorkflowLoader

                loader = WorkflowLoader()
                wf = loader.get_workflow(args)
                if wf is None:
                    print(f"Workflow '{args}' not found")
                    return
                print(f"Executing workflow '{args}'...")
                result = asyncio.run(wf.execute({"user_input": args}))
                if result.get("success"):
                    for step in result.get("steps", []):
                        print(f"  [{step['step_id']}] {'OK' if step['success'] else 'FAIL'}")
                        if step.get("output"):
                            print(f"    {step['output'][:200]}")
                else:
                    print(f"Workflow failed: {result.get('error', 'unknown')}")
            except Exception as e:
                print(f"Error: {e}")

    def _handle_discover(self):
        """Handle /discover command."""
        print("Run 'moa discover --run' in terminal to discover free models.")

    def _handle_params(self, args: str):
        """Handle /params command."""
        print("Run 'moa params --list' in terminal to list parameter templates.")

    def _show_help(self):
        """Show help."""
        print("Commands:")
        print("  /model <name>     Switch model")
        print("  /moa              Toggle MoA orchestration mode")
        print("  /clear            Clear conversation history")
        print("  /history          Show conversation history")
        print("  /workflow [name]  List or execute workflows")
        print("  /discover         Discover free models")
        print("  /params           Show parameter templates")
        print("  /ai <text>        AI command suggestion")
        print("  /blocks [N]       Show last N interaction blocks")
        print("  /export [file]    Export session history as JSON")
        print("  /help             Show this help")
        print("  /quit             Exit")
        print()
        print("AI Suggestion:")
        print("  # <description>   Get CLI command suggestions")

    def _send(self, message: str):
        """Send a message to the gateway.

        Creates USER_INPUT and AI_RESPONSE blocks for the interaction.
        """
        import httpx

        self.history.append({"role": "user", "content": message})

        # Create USER_INPUT block
        user_block_id = self._blocks.add_block(ChatBlock(
            id=str(uuid4()),
            type=BlockType.USER_INPUT,
            title="User Message",
            content=message,
        ))

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            if self.moa_mode:
                r = httpx.post(
                    f"{self.base_url}/v1/moa/execute",
                    json={
                        "model": "auto",
                        "messages": [{"role": "user", "content": message}],
                        "preset": "balanced",
                    },
                    headers=headers,
                    timeout=120,
                )
            else:
                r = httpx.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": self.history,
                        "stream": False,
                    },
                    headers=headers,
                    timeout=60,
                )

            if r.status_code == 200:
                data = r.json()
                if self.moa_mode:
                    content = (
                        data.get("final_content")
                        or data.get("aggregated_content")
                        or str(data)[:500]
                    )
                else:
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                print(content)
                self.history.append({"role": "assistant", "content": content})

                # Create AI_RESPONSE block
                self._blocks.add_block(ChatBlock(
                    id=str(uuid4()),
                    type=BlockType.AI_RESPONSE,
                    title="AI Response",
                    content=content,
                    metadata={
                        "model": self.model,
                        "moa_mode": self.moa_mode,
                        "status_code": r.status_code,
                    },
                ))
            else:
                error_msg = f"Error {r.status_code}: {r.text[:200]}"
                print(error_msg)
                self._blocks.add_block(ChatBlock(
                    id=str(uuid4()),
                    type=BlockType.ERROR,
                    title="HTTP Error",
                    content=error_msg,
                    status=BlockStatus.FAILURE,
                    metadata={"status_code": r.status_code},
                ))
        except Exception as e:
            error_msg = f"Error: {e}"
            print(error_msg)
            self._blocks.add_block(ChatBlock(
                id=str(uuid4()),
                type=BlockType.ERROR,
                title="Connection Error",
                content=error_msg,
                status=BlockStatus.FAILURE,
            ))


if __name__ == "__main__":
    import os

    repl = ChatREPL(
        model=os.environ.get("MOA_CHAT_MODEL", "auto"),
        base_url=os.environ.get("MOA_GATEWAY_URL", "http://127.0.0.1:8910"),
        api_key=os.environ.get("MOA_API_KEY", ""),
    )
    repl.run()
