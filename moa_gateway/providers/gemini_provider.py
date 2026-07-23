"""moa_gateway.providers.gemini_provider -- Google Gemini API adapter

Gemini uses a different API format from OpenAI:
- Auth: x-goog-api-key header or ?key=xxx query param
- Models: GET /v1beta/models
- Chat: POST /v1beta/models/{model}:generateContent
- Request body: contents array (role + parts), not messages
- Response body: candidates array
- system messages go through systemInstruction field
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import ChatRequest, ChatResponse, Provider, ProviderError

logger = logging.getLogger(__name__)

# OpenAI role -> Gemini role mapping
_ROLE_MAP = {"assistant": "model", "user": "user", "tool": "user"}


class GeminiProvider(Provider):
    """Google Gemini API adapter.

    Key differences from OpenAI format:
    - contents array (role + parts) replaces messages
    - systemInstruction replaces system message
    - generationConfig replaces top-level params
    - Response candidates[].content.parts[] replaces choices[].message.content
    - usageMetadata replaces usage
    """

    DEFAULT_BASE = "https://generativelanguage.googleapis.com"

    def __init__(self, api_base: str = "", api_key: str = "", timeout: int = 120, **kwargs):
        if not api_base:
            api_base = self.DEFAULT_BASE
        super().__init__(api_base, api_key, timeout)

    def _build_headers(self) -> dict[str, str]:
        """Build Gemini request headers."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        return headers

    @staticmethod
    def _convert_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert OpenAI messages to Gemini contents format.

        Returns: (system_instruction, contents)
        """
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            # system messages -> systemInstruction
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and "text" in part:
                            system_parts.append(part["text"])
                        elif isinstance(part, str):
                            system_parts.append(part)
                continue

            gemini_role = _ROLE_MAP.get(role, "user")
            parts: list[dict[str, Any]] = []

            # assistant tool_calls (content usually None)
            if content is None and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, ValueError):
                            args = {}
                    parts.append(
                        {
                            "functionCall": {
                                "name": fn.get("name", ""),
                                "args": args,
                            }
                        }
                    )
            # tool response messages
            elif role == "tool":
                tool_name = msg.get("tool_call_id", "")
                if isinstance(content, str):
                    try:
                        result = json.loads(content)
                    except (json.JSONDecodeError, ValueError):
                        result = {"result": content}
                else:
                    result = content if content is not None else {}
                parts.append(
                    {
                        "functionResponse": {
                            "name": tool_name,
                            "response": result,
                        }
                    }
                )
            # normal text / multimodal content
            else:
                if isinstance(content, str):
                    parts.append({"text": content})
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            if "text" in part:
                                parts.append({"text": part["text"]})
                            elif part.get("type") == "text":
                                parts.append({"text": part.get("text", "")})
                            elif part.get("type") == "image_url":
                                image_url = part.get("image_url", {}).get("url", "")
                                if image_url.startswith("data:"):
                                    # data:image/png;base64,xxxx
                                    mime_part, _, data = image_url.partition(",")
                                    mime_type = "image/png"
                                    if ":" in mime_part:
                                        mime_type = mime_part.split(":")[1].split(";")[0]
                                    parts.append(
                                        {
                                            "inlineData": {
                                                "mimeType": mime_type,
                                                "data": data,
                                            }
                                        }
                                    )

            if parts:
                contents.append({"role": gemini_role, "parts": parts})

        system_instruction = "\n".join(system_parts).strip()
        return system_instruction, contents

    @staticmethod
    def _build_tool_config(tool_choice: Any) -> dict[str, Any] | None:
        """Convert OpenAI tool_choice to Gemini toolConfig."""
        if tool_choice is None or tool_choice == "auto":
            return None
        if tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        if tool_choice in ("required", "any"):
            return {"functionCallingConfig": {"mode": "ANY"}}
        if isinstance(tool_choice, dict):
            fn_name = tool_choice.get("function", {}).get("name")
            if fn_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [fn_name],
                    }
                }
            return {"functionCallingConfig": {"mode": "ANY"}}
        return None

    async def chat(self, req: ChatRequest) -> ChatResponse:
        system_instruction, contents = self._convert_messages(req.messages)

        # strip "models/" prefix if present
        model = req.model
        if model.startswith("models/"):
            model = model[len("models/"):]

        url = f"{self.api_base}/v1beta/models/{model}:generateContent"

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": req.temperature,
                "maxOutputTokens": req.max_tokens,
                "topP": req.top_p,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if req.stop:
            payload["generationConfig"]["stopSequences"] = req.stop
        if req.tools:
            func_decls = []
            for t in req.tools:
                if "function" in t:
                    fn = t["function"]
                    func_decls.append(
                        {
                            "name": fn.get("name", ""),
                            "description": fn.get("description", ""),
                            "parameters": fn.get(
                                "parameters", {"type": "object", "properties": {}}
                            ),
                        }
                    )
            if func_decls:
                payload["tools"] = [{"functionDeclarations": func_decls}]
        tool_config = self._build_tool_config(req.tool_choice)
        if tool_config:
            payload["toolConfig"] = tool_config
        for k, v in req.extra.items():
            payload[k] = v

        headers = self._build_headers()
        start = time.time()
        try:
            resp = await self.client.post(
                url, json=payload, headers=headers, timeout=httpx.Timeout(req.timeout)
            )
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502) from e

        if resp.status_code != 200:
            raise ProviderError(
                f"HTTP {resp.status_code}: {resp.text[:500]}", status=resp.status_code
            )

        data = resp.json()
        candidates = data.get("candidates") or []

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        finish_reason = "stop"

        if candidates:
            candidate = candidates[0]
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            for part in parts:
                if "text" in part:
                    text_parts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        {
                            "id": f"call_{len(tool_calls)}",
                            "type": "function",
                            "function": {
                                "name": fc.get("name", ""),
                                "arguments": json.dumps(
                                    fc.get("args", {}), ensure_ascii=False
                                ),
                            },
                        }
                    )
            # map Gemini finishReason -> OpenAI finish_reason
            fr = candidate.get("finishReason", "STOP")
            finish_map = {
                "STOP": "stop",
                "MAX_TOKENS": "length",
                "SAFETY": "content_filter",
                "RECITATION": "content_filter",
                "OTHER": "stop",
            }
            finish_reason = finish_map.get(fr, "stop")

        usage_meta = data.get("usageMetadata") or {}

        return ChatResponse(
            content="".join(text_parts),
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
            prompt_tokens=usage_meta.get("promptTokenCount", 0) or 0,
            completion_tokens=usage_meta.get("candidatesTokenCount", 0) or 0,
            total_tokens=usage_meta.get("totalTokenCount", 0) or 0,
            model=req.model,
            provider="gemini",
            latency_ms=(time.time() - start) * 1000,
            cost=0.0,
            raw=data,
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """Gemini streaming via streamGenerateContent with alt=sse."""
        system_instruction, contents = self._convert_messages(req.messages)
        model = req.model
        if model.startswith("models/"):
            model = model[len("models/"):]

        url = f"{self.api_base}/v1beta/models/{model}:streamGenerateContent?alt=sse"

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": req.temperature,
                "maxOutputTokens": req.max_tokens,
                "topP": req.top_p,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        for k, v in req.extra.items():
            payload[k] = v

        headers = self._build_headers()
        try:
            async with self.client.stream(
                "POST", url, json=payload, headers=headers, timeout=httpx.Timeout(req.timeout)
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderError(
                        f"Stream HTTP {resp.status_code}: {body[:300]!r}",
                        status=resp.status_code,
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                    except Exception:
                        continue
                    candidates = d.get("candidates") or []
                    if candidates:
                        parts = (candidates[0].get("content") or {}).get("parts") or []
                        for part in parts:
                            if "text" in part:
                                yield part["text"]
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502) from e

    async def health_check(self) -> bool:
        """GET /v1beta/models to check API availability."""
        if not self.api_key:
            return False
        try:
            url = f"{self.api_base}/v1beta/models"
            headers = self._build_headers()
            resp = await self.client.get(url, headers=headers, timeout=httpx.Timeout(8))
            return 200 <= resp.status_code < 400
        except Exception:
            return False
