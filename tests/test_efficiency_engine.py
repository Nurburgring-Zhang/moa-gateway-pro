"""M6 token-efficiency engine tests (OpenClacky port).

Covers: token estimation, double cache markers, frozen system prompt +
side channel, extractive summarizer, compression gates, tool-pair-preserving
recent selection, the full SessionCompressor pipeline with real chunk-MD
archiving, the idle scheduler, metrics, and the /v1/efficiency HTTP surface.

All route tests build their own ``FastAPI`` app with only the efficiency
router included (no dependency_overrides; auth via settings gateway keys).
"""
from __future__ import annotations

import math
import threading

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.efficiency import (
    IDLE_DELAY_SECONDS,
    EfficiencyMetrics,
    IdleCompressionScheduler,
    SessionCompressor,
    SystemPromptMutationError,
    SystemPromptRegistry,
    add_cache_control_to_message,
    apply_cache_markers,
    build_compression_message,
    calculate_target_recent_count,
    compression_needed,
    estimate_content_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
    get_metrics,
    get_recent_messages_with_tool_pairs,
    idle_compression_needed,
    make_idle_compress_task,
    parse_compressed_result,
    rebuild_with_compression,
    side_channel_message,
    strip_cache_markers,
    strip_internal_fields,
    summarize_messages,
    truncate_tool_result,
)
from moa_gateway.efficiency.tokens import MESSAGE_OVERHEAD_TOKENS

API_KEY = "eff-test-key-0001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture(autouse=True)
def _fresh_metrics():
    """Metrics singleton is process-wide; reset per test (test boundary)."""
    get_metrics().reset()
    yield
    get_metrics().reset()


@pytest.fixture
def api_key(monkeypatch):
    """Real auth path: register the test key in isolated settings. The
    Storage bootstrap additionally requires a strong admin password."""
    monkeypatch.setenv("MOA_ADMIN_PASSWORD", "EffTestP@ss99!")
    from moa_gateway.config import get_settings

    get_settings().auth.gateway_api_keys.append(API_KEY)


@pytest.fixture
async def client():
    from moa_gateway.routes.efficiency import router

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _chat(n_pairs: int, text: str = "Discussing the deployment pipeline and caching strategy.") -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "You are a helpful gateway assistant."}]
    for i in range(n_pairs):
        msgs.append({"role": "user", "content": f"{text} Turn {i} user side."})
        msgs.append({"role": "assistant", "content": f"{text} Turn {i} assistant side."})
    return msgs


def _long_chat(n_pairs: int) -> list[dict]:
    """Messages of ~1300 chars each (~325 tokens) so a 35-pair batch clears
    the 20K idle-token floor and the max_recent+1 message gate."""
    filler = "The gateway caches prompt prefixes and compresses idle sessions. " * 20
    msgs: list[dict] = [{"role": "system", "content": "You are the gateway."}]
    for i in range(n_pairs):
        msgs.append({"role": "user", "content": f"{filler} question {i}"})
        msgs.append({"role": "assistant", "content": f"{filler} answer {i}"})
    return msgs


# ─────────────────────────── tokens ───────────────────────────


class TestTokenEstimation:
    def test_ascii_string_quarter_rate(self):
        # 400 printable ASCII chars -> 100 tokens, no overhead for raw content.
        assert estimate_content_tokens("a" * 400) == 100

    def test_multibyte_weighted_higher(self):
        text = "测" * 30  # 30 multibyte chars / 1.5 = 20 tokens
        assert estimate_content_tokens(text) == 20

    def test_message_overhead_applied(self):
        msg = {"role": "user", "content": "a" * 40}
        assert estimate_message_tokens(msg) == MESSAGE_OVERHEAD_TOKENS + 10

    def test_content_blocks_and_tool_calls_counted(self):
        msg = {
            "role": "assistant",
            "content": [{"type": "text", "text": "a" * 40}],
            "tool_calls": [
                {"function": {"name": "search", "arguments": {"q": "a" * 40}}}
            ],
        }
        tokens = estimate_message_tokens(msg)
        assert tokens >= MESSAGE_OVERHEAD_TOKENS + 20  # text + args both counted

    def test_batch_total_is_sum(self):
        msgs = [{"role": "user", "content": "a" * 40} for _ in range(3)]
        assert estimate_messages_tokens(msgs) == 3 * estimate_message_tokens(msgs[0])

    def test_ceiling_rounding(self):
        # 5 ASCII chars -> 1.25 -> ceil 2 (math.ceil path).
        assert estimate_content_tokens("abcde") == math.ceil(5 / 4)


# ─────────────────────────── cache markers ───────────────────────────


class TestCacheMarkers:
    def test_double_marker_on_last_two(self):
        msgs = _chat(3)
        marked, indices = apply_cache_markers(msgs)
        assert indices == [len(msgs) - 2, len(msgs) - 1]
        for idx in indices:
            blocks = marked[idx]["content"]
            assert isinstance(blocks, list)
            assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
        # Earlier messages stay untouched strings.
        assert isinstance(marked[0]["content"], str)

    def test_system_injected_skipped_as_candidate(self):
        msgs = _chat(2) + [side_channel_message("dynamic note")]
        marked, indices = apply_cache_markers(msgs)
        assert len(msgs) - 1 not in indices  # injected msg never marked
        assert indices == [len(msgs) - 3, len(msgs) - 2]
        assert "cache_control" not in str(marked[-1])

    def test_marker_lands_on_last_block_only(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        }
        out = add_cache_control_to_message(msg)
        assert "cache_control" not in out["content"][0]
        assert out["content"][1]["cache_control"] == {"type": "ephemeral"}
        # Original never mutated.
        assert "cache_control" not in msg["content"][1]

    def test_disabled_returns_deepcopy_without_markers(self):
        msgs = _chat(2)
        marked, indices = apply_cache_markers(msgs, enabled=False)
        assert indices == []
        assert marked == msgs
        assert marked is not msgs

    def test_single_message_gets_one_marker(self):
        marked, indices = apply_cache_markers([{"role": "user", "content": "hi"}])
        assert indices == [0]

    def test_strip_markers_inverse(self):
        msgs = _chat(2)
        marked, _ = apply_cache_markers(msgs)
        stripped = strip_cache_markers(marked)
        # String content is unwrapped back to the original shape.
        assert stripped[-1]["content"] == msgs[-1]["content"]
        assert stripped[-2]["content"] == msgs[-2]["content"]

    def test_custom_marker_count(self):
        msgs = _chat(4)
        _, indices = apply_cache_markers(msgs, marker_count=3)
        assert indices == [len(msgs) - 3, len(msgs) - 2, len(msgs) - 1]


# ─────────────────────────── frozen system prompt ───────────────────────────


class TestSystemPromptRegistry:
    def test_freeze_first_wins_and_idempotent(self):
        reg = SystemPromptRegistry()
        f1 = reg.freeze("s1", "You are X.")
        f2 = reg.freeze("s1", "You are X.")
        assert f1 is f2
        assert f1.verify("You are X.")

    def test_mutation_refused(self):
        reg = SystemPromptRegistry()
        reg.freeze("s1", "original")
        with pytest.raises(SystemPromptMutationError):
            reg.freeze("s1", "changed")

    def test_side_channel_layout(self):
        reg = SystemPromptRegistry()
        reg.freeze("s1", "system body")
        msgs = [
            {"role": "system", "content": "rogue second system"},
            {"role": "user", "content": "hello"},
        ]
        out = reg.inject_side_channel("s1", "current time: 12:00", msgs)
        assert out[0] == {"role": "system", "content": "system body"}
        assert all(m.get("role") != "system" for m in out[1:-1])
        tail = out[-1]
        assert tail["system_injected"] is True
        assert tail["role"] == "user"
        assert "current time" in tail["content"]

    def test_inject_unfrozen_without_content_raises(self):
        reg = SystemPromptRegistry()
        with pytest.raises(KeyError):
            reg.inject_side_channel("missing", "x", [])

    def test_inject_detects_tampered_system_content(self):
        reg = SystemPromptRegistry()
        reg.freeze("s1", "original")
        with pytest.raises(SystemPromptMutationError):
            reg.inject_side_channel("s1", "note", [], system_content="tampered")

    def test_strip_internal_fields(self):
        msgs = [
            side_channel_message("n", compressed_summary=True, chunk_path="/x", topics="a"),
            {"role": "user", "content": "plain"},
        ]
        out = strip_internal_fields(msgs)
        assert set(out[0].keys()) == {"role", "content"}
        assert out[1] == msgs[1]


# ─────────────────────────── summarizer ───────────────────────────


class TestSummarizer:
    def test_summary_within_budget_and_has_substance(self):
        msgs = _chat(12)
        res = summarize_messages(msgs, target_tokens=300, level=1)
        assert res.text.strip()
        assert res.sentences_used >= 1
        assert res.estimated_tokens <= 300 + 64  # header allowance documented
        assert "deployment" in res.text.lower() or "caching" in res.text.lower()

    def test_level3_collapses_to_counts_only(self):
        msgs = _chat(4)
        res = summarize_messages(msgs, target_tokens=300, level=3)
        assert res.sentences_used == 0
        assert "Project progress" in res.text
        assert "requests" in res.text

    def test_extract_topics_finds_repeated_keywords(self):
        msgs = _chat(6)  # repeats "deployment", "pipeline", "caching", "strategy"
        res = summarize_messages(msgs, target_tokens=500)
        assert res.topics
        assert any(kw in res.topics for kw in ("deployment", "caching", "pipeline"))

    def test_empty_messages_still_frame(self):
        res = summarize_messages([], target_tokens=100)
        assert res.text.strip()
        assert res.sentences_used == 0


# ─────────────────────────── compression gates & helpers ───────────────────────────


class TestGatesAndHelpers:
    def test_compression_needed_either_threshold(self):
        assert compression_needed(200_000, 10, 150_000, 200, 10_000)
        assert compression_needed(50_000, 250, 150_000, 200, 10_000)
        assert not compression_needed(50_000, 10, 150_000, 200, 10_000)

    def test_ten_percent_minimal_reduction_guard(self):
        # Tokens exceed threshold but reduction < 10% -> skip.
        assert not compression_needed(155_000, 10, 150_000, 200, 145_000)

    def test_idle_gate_requires_message_surplus_and_floor(self):
        assert idle_compression_needed(25_000, 30, 20_000, 20)
        assert not idle_compression_needed(25_000, 21, 20_000, 20)  # <= max+1
        assert not idle_compression_needed(10_000, 30, 20_000, 20)  # below floor

    def test_recent_selection_keeps_tool_pairs(self):
        msgs = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "search", "arguments": {}}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "assistant", "content": "final answer"},
        ]
        recent = get_recent_messages_with_tool_pairs(msgs, 2)
        roles = [m.get("role") for m in recent]
        # Selecting 2 from the tail must not orphan the tool result from its
        # assistant call: the assistant+result pair is pulled back in.
        assert "tool" in roles
        assert roles.count("assistant") >= 1
        tool_idx = roles.index("tool")
        assert roles[:tool_idx].count("assistant") >= 1

    def test_truncate_tool_result_caps_long_content(self):
        msg = {"role": "tool", "content": "x" * 5000}
        out = truncate_tool_result(msg, max_chars=2000)
        assert len(out["content"]) < 5000
        assert "truncated" in out["content"]
        assert out is not msg

    def test_calculate_target_recent_count_bounds(self):
        assert calculate_target_recent_count(140_000, 10_000, 20) == 4  # 2000 // 500
        assert calculate_target_recent_count(1, 10_000, 20) >= 1
        assert calculate_target_recent_count(10**9, 1_000_000, 20) == 20  # capped

    def test_build_compression_message_none_when_all_recent(self):
        msgs = [{"role": "system", "content": "s"}]
        assert build_compression_message(msgs, recent_messages=[]) is None
        msgs2 = _chat(2)
        cm = build_compression_message(msgs2, recent_messages=[])
        assert cm["system_injected"] is True and cm["role"] == "user"

    def test_parse_and_rebuild_roundtrip(self):
        msgs = _chat(3)
        rebuilt = rebuild_with_compression(
            "<topics>a, b</topics><summary>The summary body.</summary>",
            original_messages=msgs,
            recent_messages=msgs[-2:],
            chunk_path="/tmp/chunk-1.md",
            topics="a, b",
        )
        assert rebuilt[0]["role"] == "system"
        summary_msg = rebuilt[1]
        assert summary_msg["compressed_summary"] is True
        assert "The summary body." in summary_msg["content"]
        assert "<topics>" not in summary_msg["content"]
        assert "chunk-1.md" in summary_msg["content"]
        with pytest.raises(ValueError):
            rebuild_with_compression("", msgs, msgs[-2:])


# ─────────────────────────── SessionCompressor pipeline ───────────────────────────


class TestSessionCompressor:
    def test_below_threshold_noop(self):
        engine = SessionCompressor()
        res = engine.compress(_chat(3), "sess-noop")
        assert res.compressed is False
        assert "below thresholds" in res.reason
        assert res.messages

    def test_threshold_compression_real_archive(self, tmp_path):
        engine = SessionCompressor(archive_dir=tmp_path)
        msgs = _chat(110)  # 221 messages >= 200 threshold
        res = engine.compress(msgs, "sess-A")
        assert res.compressed is True
        assert res.level == 1
        assert res.archived_messages > 0
        assert res.kept_recent >= 1
        assert res.tokens_after < res.tokens_before
        # Rebuilt shape: single system prompt + framed summary + recent.
        roles = [m.get("role") for m in res.messages]
        assert roles.count("system") == 1
        assert res.messages[1].get("compressed_summary") is True
        assert res.messages[1].get("system_injected") is True
        # REAL chunk file archived on disk with front matter.
        chunk = tmp_path / "sess-A" / "chunk-1.md"
        assert chunk.is_file()
        body = chunk.read_text(encoding="utf-8")
        assert "session_id: sess-A" in body
        assert "chunk: 1" in body
        assert res.chunk_path == str(chunk)
        # Summary references the archived chunk.
        assert "chunk-1.md" in res.messages[1]["content"]

    def test_idle_force_merge_into_previous_chunk(self, tmp_path):
        engine = SessionCompressor(archive_dir=tmp_path)
        first = engine.compress(_long_chat(110), "sess-B")
        assert first.compressed and first.merged_into_previous_chunk is False
        # Second pass with force=True (idle semantics) merges into chunk-1.
        second = engine.compress(_long_chat(110), "sess-B", force=True)
        assert second.compressed is True
        assert second.merged_into_previous_chunk is True
        assert second.level == 2
        chunk = tmp_path / "sess-B" / "chunk-1.md"
        assert chunk.is_file()
        body = chunk.read_text(encoding="utf-8")
        assert "merged_count: 2" in body
        assert not (tmp_path / "sess-B" / "chunk-2.md").exists()

    def test_second_threshold_compression_new_chunk_and_index(self, tmp_path):
        engine = SessionCompressor(archive_dir=tmp_path)
        engine.compress(_chat(110), "sess-C")
        res2 = engine.compress(_chat(110), "sess-C")  # force=False -> new chunk
        assert res2.compressed is True
        assert (tmp_path / "sess-C" / "chunk-2.md").is_file()
        # The new summary lists chunk-1 in the previous-chunks index.
        assert "chunk-1.md" in res2.messages[1]["content"]

    def test_idle_gate_not_met_is_noop(self, tmp_path):
        engine = SessionCompressor(archive_dir=tmp_path)
        res = engine.compress(_chat(5), "sess-D", force=True)
        assert res.compressed is False
        assert "idle gate not met" in res.reason

    def test_session_id_traversal_sanitized(self, tmp_path):
        engine = SessionCompressor(archive_dir=tmp_path)
        res = engine.compress(_chat(110), "../evil/../escape")
        assert res.compressed is True
        from pathlib import Path

        chunk_path = Path(res.chunk_path)
        # The sanitized id must keep the archive physically under tmp_path —
        # no path component may climb out of the archive root.
        assert chunk_path.is_file()
        assert chunk_path.parent.parent == tmp_path
        assert not chunk_path.parent.name.startswith("..")
        assert "/" not in chunk_path.parent.name and "\\" not in chunk_path.parent.name


# ─────────────────────────── idle scheduler ───────────────────────────


class TestIdleScheduler:
    def test_arm_fires_after_delay(self):
        fired = threading.Event()
        seen: dict = {}

        def on_compress(session_id, success):
            seen["session"] = session_id
            seen["success"] = success
            fired.set()

        sched = IdleCompressionScheduler(on_compress=on_compress, delay_s=0.05)
        assert sched.arm("s1", lambda: True) is True
        assert fired.wait(3.0)
        assert seen == {"session": "s1", "success": True}
        sched.shutdown()

    def test_cancel_before_fire_prevents_compression(self):
        fired = threading.Event()
        sched = IdleCompressionScheduler(on_compress=lambda *_: fired.set(), delay_s=0.3)
        sched.arm("s1", lambda: True)
        sched.cancel("s1")
        assert not fired.wait(0.6)
        sched.shutdown()

    def test_shutdown_blocks_new_arms(self):
        sched = IdleCompressionScheduler(delay_s=0.05)
        sched.shutdown()
        assert sched.is_shutdown() is True
        assert sched.arm("s1", lambda: True) is False

    def test_make_idle_compress_task_runs_real_pipeline(self, tmp_path):
        from moa_gateway.efficiency.compressor import CompressionResult

        results: list[CompressionResult] = []
        engine = SessionCompressor(archive_dir=tmp_path)
        # _long_chat: ~1280 chars/msg (~325 tokens), 35 pairs -> ~23K tokens
        # AND 71 messages > max_recent+1, so the idle gate genuinely passes.
        messages = _long_chat(35)
        provider = lambda sid: messages  # noqa: E731 (test-local messages provider)
        task = make_idle_compress_task(
            "sess-idle", provider, compressor=engine, on_compressed=lambda sid, r: results.append(r)
        )
        assert task() is True
        assert results and results[0].compressed is True
        assert (tmp_path / "sess-idle" / "chunk-1.md").is_file()

    def test_default_delay_under_cache_ttl(self):
        # The OpenClacky invariant: idle delay must stay below the 5-minute
        # provider prompt-cache TTL.
        assert IDLE_DELAY_SECONDS == 266.0
        assert IDLE_DELAY_SECONDS < 300.0


# ─────────────────────────── metrics ───────────────────────────


class TestMetrics:
    def test_hit_counted_when_cache_read_positive(self):
        m = EfficiencyMetrics()
        m.record_usage({"input_tokens": 100, "cache_read_input_tokens": 500})
        m.record_usage({"input_tokens": 100, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 300})
        snap = m.snapshot()
        assert snap["usage_reports"] == 2
        assert snap["cache_hit_requests"] == 1
        assert snap["cache_hit_rate"] == 0.5
        assert snap["total_cache_read_tokens"] == 500
        assert snap["total_cache_write_tokens"] == 300

    def test_compression_savings_tracked(self):
        m = EfficiencyMetrics()
        m.record_compression(True, tokens_before=1000, tokens_after=200, archived_messages=42)
        m.record_compression(False)
        snap = m.snapshot()
        assert snap["tokens_saved"] == 800
        assert snap["messages_archived"] == 42
        assert snap["compression_rate"] == 0.5

    def test_reset_zeroes_everything(self):
        m = EfficiencyMetrics()
        m.record_prepare(10, 2)
        m.record_usage({"cache_read_input_tokens": 1})
        m.reset()
        snap = m.snapshot()
        assert snap["prepare_calls"] == 0
        assert snap["usage_reports"] == 0


# ─────────────────────────── HTTP routes ───────────────────────────


class TestEfficiencyRoutes:
    async def test_prepare_requires_auth(self, client):
        r = await client.post("/v1/efficiency/prepare", json={"messages": _chat(2)})
        assert r.status_code == 401

    async def test_prepare_applies_double_marker(self, client, api_key):
        msgs = _chat(3)
        r = await client.post("/v1/efficiency/prepare", json={"messages": msgs}, headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["markers_applied"] == 2
        assert data["cache_control_indices"] == [5, 6]
        assert data["strategy"] == "ephemeral-double-marker"
        tail = data["messages"][-1]["content"]
        assert tail[-1]["cache_control"] == {"type": "ephemeral"}
        assert get_metrics().snapshot()["prepare_calls"] == 1

    async def test_prepare_disabled_passthrough(self, client, api_key):
        msgs = _chat(2)
        r = await client.post(
            "/v1/efficiency/prepare",
            json={"messages": msgs, "enabled": False},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["markers_applied"] == 0
        assert r.json()["messages"] == msgs

    async def test_compress_session_real_pipeline(self, client, api_key, tmp_path, monkeypatch):
        # Archive into the isolated DATA_DIR (conftest patches config.DATA_DIR).
        msgs = _chat(110)
        r = await client.post(
            "/v1/efficiency/compress-session",
            json={"messages": msgs, "session_id": "http-sess"},
            headers=AUTH,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["compressed"] is True
        assert data["archived_messages"] > 0
        import os

        assert data["chunk_path"] and os.path.isfile(data["chunk_path"])
        snap = get_metrics().snapshot()
        assert snap["compressions_done"] == 1
        assert snap["tokens_saved"] > 0

    async def test_metrics_endpoint_reflects_activity(self, client, api_key):
        await client.post(
            "/v1/efficiency/prepare",
            json={"messages": _chat(2)},
            headers=AUTH,
        )
        r = await client.get("/v1/efficiency/metrics", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["prepare_calls"] == 1
        assert body["markers_applied"] == 2
        assert "cache_hit_rate" in body

    async def test_capability_toggle_gates_503(self, client, api_key, monkeypatch):
        import moa_gateway.capability_toggles as toggles

        # Test boundary: manipulate the in-memory toggle cache directly;
        # restore happens via conftest/monkeypatch teardown.
        cache = dict(toggles.DEFAULT_CAPABILITIES)
        cache["token_efficiency"] = False
        monkeypatch.setattr(toggles, "_cache", cache)
        r = await client.get("/v1/efficiency/metrics", headers=AUTH)
        assert r.status_code == 503
        assert "token_efficiency" in r.json()["detail"]
