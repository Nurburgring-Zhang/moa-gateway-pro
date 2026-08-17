"""Assistant API data models."""
from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


class Assistant(BaseModel):
    id: str = Field(default_factory=lambda: _gen_id("asst"))
    object: str = "assistant"
    created_at: int = Field(default_factory=lambda: int(time.time()))
    name: str | None = None
    description: str | None = None
    model: str = "gpt-4o"
    instructions: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    temperature: float = 1.0
    top_p: float = 1.0
    owner_key_id: str = ""


class Thread(BaseModel):
    id: str = Field(default_factory=lambda: _gen_id("thread"))
    object: str = "thread"
    created_at: int = Field(default_factory=lambda: int(time.time()))
    metadata: dict[str, Any] = Field(default_factory=dict)
    owner_key_id: str = ""


class Message(BaseModel):
    id: str = Field(default_factory=lambda: _gen_id("msg"))
    object: str = "thread.message"
    created_at: int = Field(default_factory=lambda: int(time.time()))
    thread_id: str = ""
    role: str = "user"  # user | assistant
    content: list[dict[str, Any]] = Field(default_factory=list)
    assistant_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    id: str = Field(default_factory=lambda: _gen_id("run"))
    object: str = "thread.run"
    created_at: int = Field(default_factory=lambda: int(time.time()))
    thread_id: str = ""
    assistant_id: str = ""
    status: str = "queued"  # queued|in_progress|requires_action|completed|failed|cancelled
    model: str = "gpt-4o"
    instructions: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: int | None = None
    completed_at: int | None = None
    failed_at: int | None = None
    last_error: dict[str, Any] | None = None
    required_action: dict[str, Any] | None = None
    usage: dict[str, int] | None = None


class RunStep(BaseModel):
    id: str = Field(default_factory=lambda: _gen_id("step"))
    object: str = "thread.run.step"
    created_at: int = Field(default_factory=lambda: int(time.time()))
    run_id: str = ""
    thread_id: str = ""
    type: str = "message_creation"  # message_creation | tool_calls
    status: str = "in_progress"
    step_details: dict[str, Any] = Field(default_factory=dict)
    completed_at: int | None = None
    failed_at: int | None = None
    last_error: dict[str, Any] | None = None
    usage: dict[str, int] | None = None
