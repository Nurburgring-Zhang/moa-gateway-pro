"""Memory hook protocol — fail-closed command parsing ported from MemoraX Code.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``packages/ts/memorax-code-backend/src/memory/hook-command.ts``.

MemoraX's hook endpoints accept JSON commands from coding clients (Codex,
Claude Code, OpenCode, DSH).  Security model (ported verbatim):

- the command ``version`` must equal ``MEMORY_HOOK_COMMAND_VERSION`` (1);
- ``client`` must be one of the known clients;
- **every request field is checked against a per-command, per-client key
  whitelist — any unknown key rejects the whole command** (fail-closed);
- required identity fields (session + turn/prompt correlation) must be
  present, non-empty strings; incomplete identities are rejected.

This module is pure parsing/validation: no I/O, no side effects.
"""

from __future__ import annotations

from typing import Any

MEMORY_HOOK_COMMAND_VERSION = 1
INVALID_MEMORY_HOOK_COMMAND = "invalid memory Hook command"

MEMORY_HOOK_CLIENTS: tuple[str, ...] = ("codex", "claude-code", "opencode", "dsh")

SKILL_REMINDER_TRIGGERS: tuple[str, ...] = ("cadence", "post_compaction")

_BASE_COMMAND_KEYS: tuple[str, ...] = ("version", "client", "sessionId", "cwd", "workspaceKind")

# --- Per-command, per-client key whitelists (MemoraX hook-command.ts) --------
TURN_START_KEYS: dict[str, frozenset[str]] = {
    "codex": frozenset([*_BASE_COMMAND_KEYS, "turnId", "prompt", "transcriptPath"]),
    "claude-code": frozenset([*_BASE_COMMAND_KEYS, "promptId", "prompt", "transcriptPath"]),
    "opencode": frozenset([*_BASE_COMMAND_KEYS, "userMessageId", "prompt"]),
    "dsh": frozenset(["version", "client", "sessionId", "turn", "startSeq", "cwd", "prompt"]),
}

WRITEBACK_KEYS: dict[str, frozenset[str]] = {
    "codex": frozenset([*_BASE_COMMAND_KEYS, "turnId", "lastAssistantMessage", "transcriptPath"]),
    "claude-code": frozenset(
        [*_BASE_COMMAND_KEYS, "promptId", "lastAssistantMessage", "transcriptPath"]
    ),
    "opencode": frozenset(
        [*_BASE_COMMAND_KEYS, "userMessageId", "assistantMessageId", "messages"]
    ),
    "dsh": frozenset(
        [
            "version",
            "client",
            "sessionId",
            "turn",
            "startSeq",
            "endSeq",
            "cwd",
            "sessionHeader",
            "events",
        ]
    ),
}

SKILL_REMINDER_KEYS: dict[str, frozenset[str]] = {
    "codex": frozenset([*_BASE_COMMAND_KEYS, "turnId", "transcriptPath", "content", "triggers"]),
    "claude-code": frozenset(
        [*_BASE_COMMAND_KEYS, "promptId", "transcriptPath", "content", "triggers"]
    ),
    "dsh": frozenset(["version", "client", "sessionId", "turn", "cwd", "content", "triggers"]),
    "opencode": frozenset([*_BASE_COMMAND_KEYS, "userMessageId", "content", "triggers"]),
}

ParseResult = tuple[bool, dict[str, Any] | str]
"""(ok, command) on success, (False, error-string) on rejection."""


def _required_string(value: dict[str, Any], key: str) -> str | None:
    field = value.get(key)
    if isinstance(field, str) and field.strip():
        return field.strip()
    return None


def _optional_string(value: dict[str, Any], key: str) -> tuple[bool, str | None]:
    """Returns (ok, value): absent key is ok/None, present-but-invalid fails."""
    if key not in value:
        return True, None
    field = _required_string(value, key)
    if field is None:
        return False, None
    return True, field


def _positive_int(value: dict[str, Any], key: str) -> int | None:
    field = value.get(key)
    if isinstance(field, bool):
        return None
    if isinstance(field, int) and field > 0:
        return field
    return None


def _non_negative_int(value: dict[str, Any], key: str) -> int | None:
    field = value.get(key)
    if isinstance(field, bool):
        return None
    if isinstance(field, int) and field >= 0:
        return field
    return None


def _parse_command_base(
    value: dict[str, Any],
    allowed_keys: dict[str, frozenset[str]],
) -> dict[str, Any] | None:
    """Fail-closed base validation shared by all three hook commands."""
    if value.get("version") != MEMORY_HOOK_COMMAND_VERSION:
        return None
    client = value.get("client")
    if client not in MEMORY_HOOK_CLIENTS:
        return None
    client_keys = allowed_keys.get(client)
    if client_keys is None:
        return None
    # Fail-closed whitelist: ANY unknown key rejects the whole command.
    if any(key not in client_keys for key in value):
        return None
    session_id = _required_string(value, "sessionId")
    if not session_id:
        return None
    cwd_ok, cwd = _optional_string(value, "cwd")
    kind_ok, workspace_kind = _optional_string(value, "workspaceKind")
    if not cwd_ok or not kind_ok:
        return None
    base: dict[str, Any] = {
        "version": MEMORY_HOOK_COMMAND_VERSION,
        "client": client,
        "sessionId": session_id,
    }
    if cwd:
        base["cwd"] = cwd
    if workspace_kind:
        base["workspaceKind"] = workspace_kind
    return base


def parse_turn_start_command(value: Any) -> ParseResult:
    """Parse a /memory/turn-start hook command (fail-closed whitelist)."""
    if not isinstance(value, dict):
        return False, INVALID_MEMORY_HOOK_COMMAND
    base = _parse_command_base(value, TURN_START_KEYS)
    if base is None:
        return False, INVALID_MEMORY_HOOK_COMMAND
    prompt = _required_string(value, "prompt")
    if not prompt:
        return False, INVALID_MEMORY_HOOK_COMMAND
    client = base["client"]
    if client == "dsh":
        turn = _positive_int(value, "turn")
        start_seq = _non_negative_int(value, "startSeq")
        if turn is None or start_seq is None or "cwd" not in base:
            return False, INVALID_MEMORY_HOOK_COMMAND
        return True, {**base, "turn": turn, "startSeq": start_seq, "prompt": prompt}
    if client == "codex":
        transcript_path = _required_string(value, "transcriptPath")
        turn_id_ok, turn_id = _optional_string(value, "turnId")
        if not transcript_path or not turn_id_ok:
            return False, INVALID_MEMORY_HOOK_COMMAND
        command = {**base, "prompt": prompt, "transcriptPath": transcript_path}
        if turn_id:
            command["turnId"] = turn_id
        return True, command
    if client == "opencode":
        user_message_id = _required_string(value, "userMessageId")
        if not user_message_id:
            return False, INVALID_MEMORY_HOOK_COMMAND
        return True, {**base, "userMessageId": user_message_id, "prompt": prompt}
    # claude-code
    prompt_id = _required_string(value, "promptId")
    transcript_path = _required_string(value, "transcriptPath")
    if not prompt_id or not transcript_path:
        return False, INVALID_MEMORY_HOOK_COMMAND
    return True, {
        **base,
        "promptId": prompt_id,
        "prompt": prompt,
        "transcriptPath": transcript_path,
    }


def parse_writeback_command(value: Any) -> ParseResult:
    """Parse a /memory/writeback hook command (fail-closed whitelist)."""
    if not isinstance(value, dict):
        return False, INVALID_MEMORY_HOOK_COMMAND
    base = _parse_command_base(value, WRITEBACK_KEYS)
    if base is None:
        return False, INVALID_MEMORY_HOOK_COMMAND
    client = base["client"]
    if client == "dsh":
        turn = _positive_int(value, "turn")
        start_seq = _non_negative_int(value, "startSeq")
        end_seq = _non_negative_int(value, "endSeq")
        if (
            turn is None
            or start_seq is None
            or end_seq is None
            or end_seq < start_seq
            or "cwd" not in base
            or not isinstance(value.get("sessionHeader"), dict)
            or not isinstance(value.get("events"), list)
        ):
            return False, INVALID_MEMORY_HOOK_COMMAND
        return True, {
            **base,
            "turn": turn,
            "startSeq": start_seq,
            "endSeq": end_seq,
            "sessionHeader": value["sessionHeader"],
            "events": value["events"],
        }
    if client == "opencode":
        user_message_id = _required_string(value, "userMessageId")
        assistant_message_id = _required_string(value, "assistantMessageId")
        if (
            not user_message_id
            or not assistant_message_id
            or not isinstance(value.get("messages"), list)
        ):
            return False, INVALID_MEMORY_HOOK_COMMAND
        return True, {
            **base,
            "userMessageId": user_message_id,
            "assistantMessageId": assistant_message_id,
            "messages": value["messages"],
        }
    last_assistant_message = _required_string(value, "lastAssistantMessage")
    if not last_assistant_message:
        return False, INVALID_MEMORY_HOOK_COMMAND
    if client == "codex":
        turn_id_ok, turn_id = _optional_string(value, "turnId")
        path_ok, transcript_path = _optional_string(value, "transcriptPath")
        if not turn_id_ok or not path_ok:
            return False, INVALID_MEMORY_HOOK_COMMAND
        command = {**base, "lastAssistantMessage": last_assistant_message}
        if turn_id:
            command["turnId"] = turn_id
        if transcript_path:
            command["transcriptPath"] = transcript_path
        return True, command
    # claude-code
    prompt_id = _required_string(value, "promptId")
    transcript_path = _required_string(value, "transcriptPath")
    if not prompt_id or not transcript_path:
        return False, INVALID_MEMORY_HOOK_COMMAND
    return True, {
        **base,
        "promptId": prompt_id,
        "lastAssistantMessage": last_assistant_message,
        "transcriptPath": transcript_path,
    }


def parse_skill_reminder_command(value: Any) -> ParseResult:
    """Parse a /memory/skill-reminder hook command (fail-closed whitelist)."""
    if not isinstance(value, dict):
        return False, INVALID_MEMORY_HOOK_COMMAND
    base = _parse_command_base(value, SKILL_REMINDER_KEYS)
    if base is None:
        return False, INVALID_MEMORY_HOOK_COMMAND
    content = value.get("content")
    if not isinstance(content, str) or not content.strip():
        return False, INVALID_MEMORY_HOOK_COMMAND
    triggers_raw = value.get("triggers")
    if not isinstance(triggers_raw, list) or not triggers_raw:
        return False, INVALID_MEMORY_HOOK_COMMAND
    triggers: list[str] = []
    for trigger in triggers_raw:
        if trigger not in SKILL_REMINDER_TRIGGERS:
            return False, INVALID_MEMORY_HOOK_COMMAND
        if trigger not in triggers:
            triggers.append(trigger)
    client = base["client"]
    if client == "dsh":
        turn = _positive_int(value, "turn")
        if turn is None or "cwd" not in base:
            return False, INVALID_MEMORY_HOOK_COMMAND
        return True, {**base, "turn": turn, "content": content, "triggers": triggers}
    if client == "opencode":
        user_message_id = _required_string(value, "userMessageId")
        if not user_message_id:
            return False, INVALID_MEMORY_HOOK_COMMAND
        return True, {**base, "userMessageId": user_message_id, "content": content, "triggers": triggers}
    transcript_path = _required_string(value, "transcriptPath")
    if not transcript_path:
        return False, INVALID_MEMORY_HOOK_COMMAND
    if client == "codex":
        turn_id = _required_string(value, "turnId")
        if not turn_id:
            return False, INVALID_MEMORY_HOOK_COMMAND
        return True, {
            **base,
            "turnId": turn_id,
            "transcriptPath": transcript_path,
            "content": content,
            "triggers": triggers,
        }
    # claude-code
    prompt_id = _required_string(value, "promptId")
    if not prompt_id:
        return False, INVALID_MEMORY_HOOK_COMMAND
    return True, {
        **base,
        "promptId": prompt_id,
        "transcriptPath": transcript_path,
        "content": content,
        "triggers": triggers,
    }


def turn_correlation_id(command: dict[str, Any]) -> str:
    """Extract the per-client turn correlation id from a parsed command.

    MemoraX keys turn state by (client, sessionId, clientTurnId); the turn id
    field name differs per client.
    """
    client = command.get("client")
    if client == "codex":
        return str(command.get("turnId") or "")
    if client == "claude-code":
        return str(command.get("promptId") or "")
    if client == "opencode":
        return str(command.get("userMessageId") or "")
    if client == "dsh":
        return str(command.get("turn") or "")
    return ""
