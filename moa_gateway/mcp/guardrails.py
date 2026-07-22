"""Tool call guardrails - pre/post call safety hooks."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

# Dangerous patterns that should never pass through tool arguments
_BLOCKED_PATTERNS = [
    r"rm\s+-rf",
    r"DROP\s+TABLE",
    r"DELETE\s+FROM.*WHERE\s+1\s*=\s*1",
    r"FORMAT\s+[A-Z]:",
    r"exec\s*\(",
    r"eval\s*\(",
    r"__import__\s*\(",
]


class GuardrailEngine:
    """Pre/Post call guardrails for tool safety."""

    def __init__(self, blocked_patterns: Optional[List[str]] = None):
        self._pre_hooks: List[Callable] = []
        self._post_hooks: List[Callable] = []
        self._blocked_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (blocked_patterns or _BLOCKED_PATTERNS)
        ]

    async def pre_call(
        self, tool_name: str, arguments: dict[str, Any], user: Optional[dict] = None
    ) -> dict[str, Any]:
        """Pre-call validation: check for dangerous patterns in arguments."""
        self._check_dangerous_input(arguments)

        for hook in self._pre_hooks:
            arguments = await hook(tool_name, arguments, user)

        return arguments

    async def post_call(
        self, tool_name: str, result: Any, user: Optional[dict] = None
    ) -> Any:
        """Post-call processing: output filtering, PII redaction, etc."""
        for hook in self._post_hooks:
            result = await hook(tool_name, result, user)
        return result

    def add_pre_hook(self, hook: Callable) -> None:
        """Add a pre-call hook: async fn(tool_name, arguments, user) -> arguments."""
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable) -> None:
        """Add a post-call hook: async fn(tool_name, result, user) -> result."""
        self._post_hooks.append(hook)

    def _check_dangerous_input(self, arguments: dict[str, Any]) -> None:
        """Scan argument values for dangerous patterns."""
        for key, value in arguments.items():
            if isinstance(value, str):
                for pattern in self._blocked_patterns:
                    if pattern.search(value):
                        logger.warning(
                            "Guardrail BLOCKED dangerous pattern in arg '%s': %s",
                            key,
                            pattern.pattern,
                        )
                        raise ValueError(
                            f"Blocked dangerous pattern in argument '{key}'"
                        )
            elif isinstance(value, dict):
                self._check_dangerous_input(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        for pattern in self._blocked_patterns:
                            if pattern.search(item):
                                raise ValueError(
                                    f"Blocked dangerous pattern in argument '{key}'"
                                )
                    elif isinstance(item, dict):
                        self._check_dangerous_input(item)
