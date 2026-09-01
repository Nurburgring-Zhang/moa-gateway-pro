"""MultiModalFanout — 多媒体多路并发聚合引擎 (P3).

一次请求把同一模态的任务并发扇出到 N 个平台 provider，按 ``mode`` 聚合：

- ``all``     收集全部成功路由的结果（部分成功语义：失败/超时路由记证据不拖垮整体）
- ``fastest`` 任一成功后立即取第一个成功者为主结果，其余路由取消
- ``best``    全部跑完后按确定性打分选优（真实 provider 优先于 mock，其次低延迟）

设计边界（如实说明）：
- 本引擎只编排**同步可聚合**模态 —— ``image`` / ``audio_tts`` / ``audio_asr``，
  它们的 provider 方法同步返回最终产物（URL / 音频字节 / 文本），可直接聚合。
- ``music`` / ``video`` 是"建任务→轮询"的异步模态，聚合语义不成立于单一等待点，
  保留在各自的专用任务端点（/v1/audio/music、/v1/video/*），不在本引擎内假装聚合。

无密钥路由遵循全局 D6 mock 策略（与 routes/music.py 一致）：
- ``mock.mode=explicit`` 且该模态存在 Mock provider → 用带标注的 Mock provider 跑通链路
- ``mock.mode=disabled`` → 该路由标记 ``no_key`` 并给出证据，绝不静默伪造成功
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 每模态可扇出的平台 -> 环境变量密钥解析
# ---------------------------------------------------------------------------

def _clean(value: str | None) -> str:
    from .providers import is_mock_key

    v = (value or "").strip()
    return "" if is_mock_key(v) else v


def _image_key(platform_id: str) -> tuple[str, str]:
    if platform_id in ("cogview", "zhipu"):
        return (
            _clean(os.environ.get("ZHIPU_API_KEY")),
            os.environ.get("ZHIPU_API_BASE", "") or os.environ.get("COGVIEW_API_BASE", ""),
        )
    if platform_id == "wanx":
        return (
            _clean(os.environ.get("WANX_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")),
            os.environ.get("WANX_API_BASE", "") or os.environ.get("DASHSCOPE_API_BASE", ""),
        )
    if platform_id == "openai":
        return _clean(os.environ.get("OPENAI_API_KEY")), os.environ.get("OPENAI_API_BASE", "")
    return "", ""


def _tts_key(platform_id: str) -> tuple[str, str]:
    if platform_id == "minimax":
        return _clean(os.environ.get("MINIMAX_API_KEY")), os.environ.get("MINIMAX_API_BASE", "")
    if platform_id == "qwen":
        return (
            _clean(os.environ.get("DASHSCOPE_TTS_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")),
            os.environ.get("DASHSCOPE_API_BASE", ""),
        )
    if platform_id == "iflytek":
        return _clean(os.environ.get("IFLYTEK_API_KEY")), os.environ.get("IFLYTEK_API_BASE", "")
    if platform_id == "doubao":
        return _clean(os.environ.get("DOUBAO_API_KEY")), os.environ.get("DOUBAO_API_BASE", "")
    return "", ""


def _asr_key(platform_id: str) -> tuple[str, str]:
    if platform_id == "qwen":
        return (
            _clean(os.environ.get("DASHSCOPE_ASR_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")),
            os.environ.get("DASHSCOPE_API_BASE", ""),
        )
    if platform_id == "iflytek":
        return _clean(os.environ.get("IFLYTEK_API_KEY")), os.environ.get("IFLYTEK_API_BASE", "")
    return "", ""


#: modality -> (platform_id -> key resolver)
KEY_RESOLVERS: dict[str, dict[str, Callable[[str], tuple[str, str]]]] = {
    "image": {p: _image_key for p in ("cogview", "zhipu", "wanx", "openai")},
    "audio_tts": {p: _tts_key for p in ("minimax", "qwen", "iflytek", "doubao")},
    "audio_asr": {p: _asr_key for p in ("qwen", "iflytek")},
}

#: build_multimodal_provider 使用的模态键与平台 id 保持同名映射
BUILD_MODALITY: dict[str, str] = {
    "image": "image",
    "audio_tts": "audio_tts",
    "audio_asr": "audio_asr",
}


def _mock_provider_for(modality: str) -> Any | None:
    """Return a labeled mock provider for the modality, or None if不存在。"""
    if modality == "image":
        from .providers.image_generation_provider import MockImageProvider

        return MockImageProvider()
    if modality == "music":
        from .providers.music_generation_provider import MockMusicProvider

        return MockMusicProvider()
    return None


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    """单路扇出的结果与证据。"""

    platform: str
    status: str  # success | failed | timeout | no_key | cancelled | skipped_mock_unavailable
    is_mock: bool = False
    latency_ms: float = 0.0
    output: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "status": self.status,
            "is_mock": self.is_mock,
            "latency_ms": round(self.latency_ms, 2),
            "output": self.output,
            "error": self.error,
        }


@dataclass
class FanoutResult:
    modality: str
    mode: str
    routes: list[RouteResult] = field(default_factory=list)
    primary: Any = None
    any_mock: bool = False
    total_latency_ms: float = 0.0

    @property
    def successes(self) -> list[RouteResult]:
        return [r for r in self.routes if r.status == "success"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "modality": self.modality,
            "mode": self.mode,
            "routes": [r.to_dict() for r in self.routes],
            "primary": self.primary,
            "any_mock": self.any_mock,
            "success_count": len(self.successes),
            "total_latency_ms": round(self.total_latency_ms, 2),
        }


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class MultiModalFanout:
    """同模态多平台并发扇出 + 聚合。"""

    def __init__(self) -> None:
        self._mock_mode: str | None = None

    # -- mock 策略 ----------------------------------------------------------
    def _resolve_mock_mode(self) -> str:
        if self._mock_mode is not None:
            return self._mock_mode
        try:
            from .config import get_settings

            self._mock_mode = get_settings().mock.mode
        except Exception:
            self._mock_mode = "explicit"
        return self._mock_mode

    # -- provider 解析 ------------------------------------------------------
    def _resolve_provider(self, modality: str, platform_id: str) -> tuple[Any | None, bool, str]:
        """返回 (provider, is_mock, no_key_reason)。provider 为 None 表示不可执行。"""
        resolver = KEY_RESOLVERS.get(modality, {}).get(platform_id)
        if resolver is None:
            return None, False, f"unsupported platform '{platform_id}' for modality '{modality}'"

        api_key, api_base = resolver(platform_id)
        if api_key:
            from .providers import build_multimodal_provider

            prov = build_multimodal_provider(
                BUILD_MODALITY[modality], platform_id, api_key=api_key, api_base=api_base
            )
            if prov is None:
                return None, False, f"no provider registered for {modality}/{platform_id}"
            return prov, False, ""

        # 无真实密钥 → D6 策略
        if self._resolve_mock_mode() == "explicit":
            mock = _mock_provider_for(modality)
            if mock is not None:
                return mock, True, ""
            return None, False, "no_key (mock.mode=explicit but no mock provider for this modality)"
        return None, False, "no_key (mock.mode=disabled, set the platform API key)"

    # -- 单路调用 ------------------------------------------------------------
    async def _invoke(self, modality: str, provider: Any, payload: dict[str, Any]) -> Any:
        if modality == "image":
            urls = await provider.generate_image(
                payload.get("prompt", ""),
                size=payload.get("size", "1024x1024"),
                n=int(payload.get("n", 1)),
            )
            return {"urls": list(urls)}
        if modality == "audio_tts":
            audio = await provider.synthesize(
                payload.get("text", ""),
                voice=payload.get("voice", "alloy"),
                audio_format=payload.get("audio_format", "mp3"),
            )
            return {
                "audio_b64": base64.b64encode(audio).decode("ascii"),
                "bytes": len(audio),
                "format": payload.get("audio_format", "mp3"),
            }
        if modality == "audio_asr":
            raw = base64.b64decode(payload.get("audio_b64", ""))
            text = await provider.transcribe(raw, language=payload.get("language", "zh"))
            return {"text": text}
        raise ValueError(f"modality '{modality}' is not fanout-eligible")

    async def _run_route(
        self, modality: str, platform_id: str, payload: dict[str, Any], timeout_s: float
    ) -> RouteResult:
        provider, is_mock, no_key_reason = self._resolve_provider(modality, platform_id)
        if provider is None:
            return RouteResult(platform=platform_id, status="no_key", is_mock=False, error=no_key_reason)

        start = time.perf_counter()
        try:
            output = await asyncio.wait_for(self._invoke(modality, provider, payload), timeout=timeout_s)
            latency = (time.perf_counter() - start) * 1000
            return RouteResult(
                platform=platform_id, status="success", is_mock=is_mock,
                latency_ms=latency, output=output,
            )
        except asyncio.TimeoutError:
            latency = (time.perf_counter() - start) * 1000
            return RouteResult(
                platform=platform_id, status="timeout", is_mock=is_mock,
                latency_ms=latency, error=f"exceeded {timeout_s}s",
            )
        except Exception as e:  # noqa: BLE001 - 记录真实错误证据
            latency = (time.perf_counter() - start) * 1000
            logger.warning("[fanout] route %s/%s failed: %s", modality, platform_id, e)
            return RouteResult(
                platform=platform_id, status="failed", is_mock=is_mock,
                latency_ms=latency, error=f"{type(e).__name__}: {e}",
            )

    # -- 聚合 ---------------------------------------------------------------
    @staticmethod
    def _score(route: RouteResult) -> tuple[int, float]:
        """best 模式确定性打分：真实 provider 优先(0)于 mock(1)，其次低延迟。"""
        return (1 if route.is_mock else 0, route.latency_ms)

    async def execute(
        self,
        modality: str,
        platforms: list[str],
        payload: dict[str, Any],
        mode: str = "all",
        per_route_timeout_s: float = 60.0,
    ) -> FanoutResult:
        if modality not in KEY_RESOLVERS:
            raise ValueError(
                f"modality '{modality}' not fanout-eligible (use image/audio_tts/audio_asr)"
            )
        if not platforms:
            raise ValueError("platforms must not be empty")
        if mode not in ("all", "fastest", "best"):
            raise ValueError(f"unknown mode '{mode}'")

        started = time.perf_counter()
        result = FanoutResult(modality=modality, mode=mode)

        if mode == "fastest":
            await self._execute_fastest(modality, platforms, payload, per_route_timeout_s, result)
        else:
            coros = [self._run_route(modality, p, payload, per_route_timeout_s) for p in platforms]
            result.routes = list(await asyncio.gather(*coros))
            if mode == "best":
                succ = sorted(result.successes, key=self._score)
                result.primary = succ[0].output if succ else None
            else:  # all
                result.primary = [r.output for r in result.successes]

        result.any_mock = any(r.is_mock for r in result.routes if r.status == "success")
        result.total_latency_ms = (time.perf_counter() - started) * 1000
        return result

    async def _execute_fastest(
        self,
        modality: str,
        platforms: list[str],
        payload: dict[str, Any],
        timeout_s: float,
        result: FanoutResult,
    ) -> None:
        tasks = {
            asyncio.ensure_future(self._run_route(modality, p, payload, timeout_s)): p
            for p in platforms
        }
        pending = set(tasks)
        first_success: RouteResult | None = None
        finished_routes: list[RouteResult] = []

        while pending and first_success is None:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                rr = t.result()
                finished_routes.append(rr)
                if rr.status == "success":
                    first_success = rr
                    break

        # 取消尚未完成的路由，记录 cancelled 证据
        for t in pending:
            t.cancel()
            platform = tasks[t]
            finished_routes.append(RouteResult(platform=platform, status="cancelled"))

        result.routes = finished_routes
        result.primary = first_success.output if first_success else None


# 单例（与 get_tool_hub 风格一致）
_fanout: MultiModalFanout | None = None


def get_fanout() -> MultiModalFanout:
    global _fanout
    if _fanout is None:
        _fanout = MultiModalFanout()
    return _fanout
